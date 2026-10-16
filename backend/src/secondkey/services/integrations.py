"""Интеграции: приём строк закупок по API/MCP и забор из 1С по OData — всё заканчивается прогоном аудита."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import AuditRun, Connection, Organization
from secondkey.db.session import session_scope
from secondkey.integrations import crypto
from secondkey.integrations.contract import PurchasesIn
from secondkey.integrations.onec_odata import PROFILES, ODataError, OneCOData, profile_from_config
from secondkey.observability import log
from secondkey.services.audits import create_json_upload, create_run


async def submit_purchases(
    session: AsyncSession, org_id: uuid.UUID, payload: PurchasesIn, user_id: uuid.UUID | None = None
) -> AuditRun:
    """Строки из 1С (обработка, MCP, OData) → JSON-загрузка → прогон в очереди. Коммит и постановка — у вызывающего."""
    org = await session.get(Organization, org_id)
    upload = await create_json_upload(session, org_id, user_id, payload.records(), payload.source, payload.extra())
    return await create_run(session, org, upload, user_id)


def connection_public(c: Connection) -> dict[str, Any]:
    cfg = dict(c.config or {})
    return {
        "id": str(c.id),
        "kind": c.kind,
        "name": c.name,
        "enabled": c.enabled,
        "base_url": cfg.get("base_url"),
        "username": cfg.get("username"),
        "profile": cfg.get("profile"),
        "period_days": cfg.get("period_days", 90),
        "schedule": cfg.get("schedule", "manual"),
        "has_password": bool(c.secret),
        "last_sync_at": c.last_sync_at,
        "last_status": c.last_status,
        "last_error": c.last_error,
        "last_run_id": str(c.last_run_id) if c.last_run_id else None,
    }


def odata_client(config: dict[str, Any], secret: dict[str, Any], transport=None) -> OneCOData:
    return OneCOData(
        config["base_url"],
        config.get("username", ""),
        secret.get("password", ""),
        profile_from_config(config),
        transport=transport,
    )


async def test_odata(config: dict[str, Any], password: str, transport=None) -> dict[str, Any]:
    """Проверка подключения: доступна ли публикация и какой документ поступления опубликован."""
    async with odata_client(
        {**config, "profile": config.get("profile") or "bp30"}, {"password": password}, transport
    ) as c:
        detected = await c.detect_profile()
    if detected is None:
        raise ODataError(
            "В публикации OData нет документов поступления. Включите «Поступление товаров и услуг» "
            "(БП) или «Приобретение товаров и услуг» (УТ, ERP) в составе OData."
        )
    return {"profile": detected.key, "title": detected.title}


async def sync_connection(
    connection_id: uuid.UUID, start: date | None = None, end: date | None = None, transport=None
) -> uuid.UUID | None:
    """Забор строк за период и прогон аудита. Ошибка подключения сохраняется в подключении."""
    async with session_scope() as s:
        conn = await s.get(Connection, connection_id)
        if conn is None or not conn.enabled:
            return None
        config, secret_blob, org_id = dict(conn.config), conn.secret, conn.org_id
    end = end or date.today()
    start = start or end - timedelta(days=int(config.get("period_days", 90)))
    try:
        secret = crypto.unseal(secret_blob)
        async with odata_client(config, secret, transport) as c:
            records = await c.purchases(start, end)
            cards = c.supplier_cards
            pays = await c.payments(start, end)
        if not records:
            raise ODataError(f"За {start:%d.%m.%Y}–{end:%d.%m.%Y} в 1С нет проведённых документов поступления")
        payload = PurchasesIn(
            source=f"1c:{config.get('profile', 'bp30')}",
            period_start=start,
            period_end=end,
            lines=records,
            suppliers=cards,
            payments=pays,
        )
    except (ODataError, crypto.SecretsUnavailable, ValueError) as exc:
        await _mark(connection_id, "error", str(exc)[:1000])
        log.warning("onec.sync_failed", connection=str(connection_id), error=str(exc)[:300])
        return None
    async with session_scope() as s:
        run = await submit_purchases(s, org_id, payload)
        run_id = run.id
    await _mark(connection_id, "ok", None, run_id)
    from secondkey.worker.tasks import run_audit_task

    await run_audit_task.kiq(str(run_id))
    return run_id


async def _mark(connection_id: uuid.UUID, status: str, error: str | None, run_id: uuid.UUID | None = None) -> None:
    async with session_scope() as s:
        conn = await s.get(Connection, connection_id)
        if conn is None:
            return
        conn.last_sync_at, conn.last_status, conn.last_error = datetime.now(UTC), status, error
        if run_id:
            conn.last_run_id = run_id


PROFILE_CHOICES = [{"key": p.key, "title": p.title, "document": p.document} for p in PROFILES.values()]
