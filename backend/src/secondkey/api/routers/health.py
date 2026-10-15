"""Проверки живости (процесс отвечает) и готовности (БД, Redis, хранилище доступны)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from secondkey.config import settings
from secondkey.db.session import sessionmaker
from secondkey.services import progress
from secondkey.services.storage import get_storage

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        async with sessionmaker()() as s:
            await s.execute(text("select 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"
    if settings.env != "test":
        try:
            await (await progress.get_redis()).ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
    try:
        await get_storage().ensure_ready()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error: {type(exc).__name__}"
    if any(v != "ok" for v in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return checks
