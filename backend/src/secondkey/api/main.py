"""Приложение FastAPI: маршруты под /api, request-id, CORS, жизненный цикл брокера и хранилища."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from secondkey import __version__
from secondkey.api.routers import audits, auth, findings, health, integrations, org, uploads
from secondkey.config import settings
from secondkey.db.session import dispose_engine
from secondkey.observability import configure_logging, log
from secondkey.services.storage import get_storage
from secondkey.worker.broker import broker


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_json)
    if not broker.is_worker_process:
        await broker.startup()
    try:
        await get_storage().ensure_ready()
    except Exception as exc:  # API поднимается и без хранилища: /health/ready покажет проблему
        log.warning("storage.not_ready", error=str(exc))
    log.info("api.startup", env=settings.env, version=__version__)
    yield
    if not broker.is_worker_process:
        await broker.shutdown()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Second Key API",
        version=__version__,
        description="Второй ключ: независимая проверка закупок стройматериалов для собственника.",
        lifespan=lifespan,
        # в проде схема и Swagger не публикуются: типы фронта генерируются из `secondkey openapi`
        openapi_url=None if settings.env == "prod" else "/api/openapi.json",
        docs_url=None if settings.env == "prod" else "/api/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("http.unhandled", path=request.url.path, method=request.method)
            response = JSONResponse(
                {"detail": "Внутренняя ошибка сервера. Мы её видим в журнале.", "request_id": request_id},
                status_code=500,
            )
        response.headers["X-Request-Id"] = request_id
        if not request.url.path.startswith("/api/health"):
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round((time.perf_counter() - started) * 1000, 1),
            )
        return response

    for r in (
        health.router,
        auth.router,
        org.router,
        uploads.router,
        audits.router,
        findings.router,
        integrations.router,
    ):
        app.include_router(r, prefix="/api")
    return app


app = create_app()
