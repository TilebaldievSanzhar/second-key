"""Интеграции: токены, приём строк закупок из 1С, подключения OData, модуль обработки для 1С."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from secondkey.api.deps import UPLOAD, Member, Session, require
from secondkey.api.schemas import (
    ConnectionIn,
    ConnectionOut,
    ConnectionTestOut,
    SubmitOut,
    SyncIn,
    TokenCreated,
    TokenIn,
    TokenOut,
)
from secondkey.db.models import Connection, IntegrationToken, Organization
from secondkey.integrations import crypto, tokens
from secondkey.integrations.contract import PurchasesIn
from secondkey.integrations.onec_odata import ODataError
from secondkey.services import integrations as svc
from secondkey.services.orgs import inn_salt

router = APIRouter(prefix="/integrations", tags=["integrations"])

ONEC_DIR = Path(__file__).resolve().parents[2] / "integrations" / "onec_push"


def token_auth(scope: str):
    """Bearer-токен интеграции (не сессия пользователя): 1С-обработка, скрипты, MCP-клиенты."""

    async def dep(request: Request, session: Session) -> IntegrationToken:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        row = await tokens.verify(session, token, scope) if token else None
        if row is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Токен интеграции не подходит: проверьте его и права «" + scope + "» в настройках «Второго ключа»",
            )
        return row

    return Depends(dep)


# --- токены --------------------------------------------------------------------------------------


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(session: Session, member: Member = require(*UPLOAD)) -> list[IntegrationToken]:
    q = (
        select(IntegrationToken)
        .where(IntegrationToken.org_id == member.org_id)
        .order_by(IntegrationToken.created_at.desc())
    )
    return list((await session.scalars(q)).all())


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(body: TokenIn, session: Session, member: Member = require(*UPLOAD)) -> TokenCreated:
    row, token = await tokens.issue(session, member.org_id, body.name, list(body.scopes), member.user.id)
    await session.refresh(row)
    return TokenCreated(**TokenOut.model_validate(row).model_dump(), token=token)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: uuid.UUID, session: Session, member: Member = require(*UPLOAD)) -> Response:
    row = await session.get(IntegrationToken, token_id)
    if row is None or row.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Токен не найден")
    row.revoked_at = row.revoked_at or datetime.now(UTC)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- приём строк закупок по токену ---------------------------------------------------------------


@router.get("/ping")
async def ping(session: Session, tok: Annotated[IntegrationToken, token_auth("push")]) -> dict[str, str]:
    """Проверка связи из 1С: токен действителен. inn_salt — соль для хэшей ИНН сотрудников (поле staff выгрузки)."""
    org = await session.get(Organization, tok.org_id)
    salt = inn_salt(org)
    return {"status": "ok", "token": tok.name, "inn_salt": salt}


@router.post("/purchases", response_model=SubmitOut, status_code=status.HTTP_202_ACCEPTED)
async def submit(
    body: PurchasesIn, request: Request, session: Session, tok: Annotated[IntegrationToken, token_auth("push")]
) -> SubmitOut:
    """Строки закупок из 1С (обработка «Второй ключ: выгрузка закупок») или скрипта → проверка в очереди."""
    run = await svc.submit_purchases(session, tok.org_id, body)
    await session.commit()
    from secondkey.worker.tasks import run_audit_task

    await run_audit_task.kiq(str(run.id))
    await session.refresh(run)
    return SubmitOut(
        run_id=run.id,
        status=run.status,
        lines=len(body.lines),
        status_url=str(request.url_for("get_run", run_id=run.id)),
    )


# --- 1С по OData ---------------------------------------------------------------------------------


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(session: Session, member: Member = require(*UPLOAD)) -> list[dict]:
    rows = (
        await session.scalars(
            select(Connection).where(Connection.org_id == member.org_id).order_by(Connection.created_at)
        )
    ).all()
    return [svc.connection_public(c) for c in rows]


def _config(body: ConnectionIn) -> dict:
    return {
        "base_url": body.base_url.strip(),
        "username": body.username,
        "profile": body.profile,
        "period_days": body.period_days,
        "schedule": body.schedule,
    }


@router.post("/connections/test", response_model=ConnectionTestOut)
async def test_connection(
    body: ConnectionIn, session: Session, member: Member = require(*UPLOAD), connection_id: uuid.UUID | None = None
) -> ConnectionTestOut:
    """Проверка до сохранения; без пароля — с паролем сохранённого подключения connection_id."""
    password = body.password
    if not password and connection_id:
        conn = await _conn(session, connection_id, member)
        password = crypto.unseal(conn.secret).get("password", "")
    try:
        found = await svc.test_odata(_config(body), password or "")
    except ODataError as exc:
        return ConnectionTestOut(ok=False, error=str(exc))
    return ConnectionTestOut(ok=True, **found)


@router.post("/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(body: ConnectionIn, session: Session, member: Member = require(*UPLOAD)) -> dict:
    try:
        secret = crypto.seal({"password": body.password or ""})
    except crypto.SecretsUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    conn = Connection(org_id=member.org_id, kind="onec_odata", name=body.name, config=_config(body), secret=secret)
    session.add(conn)
    await session.flush()
    return svc.connection_public(conn)


async def _conn(session, connection_id: uuid.UUID, member: Member) -> Connection:
    conn = await session.get(Connection, connection_id)
    if conn is None or conn.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Подключение не найдено")
    return conn


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID, body: ConnectionIn, session: Session, member: Member = require(*UPLOAD)
) -> dict:
    conn = await _conn(session, connection_id, member)
    conn.name, conn.config = body.name, _config(body)
    if body.password:
        try:
            conn.secret = crypto.seal({"password": body.password})
        except crypto.SecretsUnavailable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.flush()
    return svc.connection_public(conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: uuid.UUID, session: Session, member: Member = require(*UPLOAD)) -> Response:
    await session.delete(await _conn(session, connection_id, member))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/connections/{connection_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync(connection_id: uuid.UUID, body: SyncIn, session: Session, member: Member = require(*UPLOAD)) -> dict:
    """Забрать строки из 1С сейчас (в фоне); итог — в подключении и в списке проверок."""
    await _conn(session, connection_id, member)
    await session.commit()
    from secondkey.worker.tasks import sync_connection_task

    await sync_connection_task.kiq(
        str(connection_id), body.start.isoformat() if body.start else None, body.end.isoformat() if body.end else None
    )
    return {"status": "queued"}


@router.get("/capabilities")
async def capabilities(member: Member = require(*UPLOAD)) -> dict[str, bool | str]:
    """Что настроено на сервере: можно ли хранить пароли 1С (SK_DATA_KEY) и включён ли ИИ (SK_LLM_API_KEY)."""
    from secondkey import llm
    from secondkey.config import settings

    return {
        "secrets": bool(settings.data_key.get_secret_value()),
        "llm": llm.enabled(),
        "public_url": settings.public_url,
    }


@router.get("/profiles")
async def profiles(member: Member = require(*UPLOAD)) -> list[dict]:
    return svc.PROFILE_CHOICES


@router.get("/1c/module.bsl", response_class=PlainTextResponse)
async def onec_module(member: Member = require(*UPLOAD)) -> PlainTextResponse:
    """Модуль внешней обработки 1С «Второй ключ: выгрузка закупок» — исходный текст для вставки в обработку."""
    text = (ONEC_DIR / "SecondKeyExport.bsl").read_text(encoding="utf-8")
    return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="SecondKeyExport.bsl"'})
