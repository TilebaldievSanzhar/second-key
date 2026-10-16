"""Задачи. Аргументы — только простые типы (JSON), состояние — в БД."""

from __future__ import annotations

import uuid

from secondkey.db.session import session_scope
from secondkey.observability import log
from secondkey.services import audits, market
from secondkey.worker.broker import broker


@broker.task(task_name="audit.run", timeout=30 * 60)
async def run_audit_task(run_id: str) -> None:
    await audits.execute_run(uuid.UUID(run_id))


# курсы НБКР публикуются утром по Бишкеку; повтор при сбое сети — SmartRetryMiddleware
@broker.task(
    task_name="refdata.fetch_fx", retry_on_error=True, schedule=[{"cron": "30 10 * * *", "cron_offset": "Asia/Bishkek"}]
)
async def fetch_fx_task() -> dict:
    async with session_scope() as s:
        res = await market.fetch_fx(s)
    log.info("fx.fetched", **res)
    return res


# эталон рынка — раз в неделю ночью: вежливо к порталу, новые закупки за неделю
@broker.task(
    task_name="market.collect_kg",
    retry_on_error=True,
    timeout=3 * 60 * 60,
    schedule=[{"cron": "0 3 * * 0", "cron_offset": "Asia/Bishkek"}],
)
async def collect_market_task(per_query: int = 40) -> dict:
    async with session_scope() as s:
        res = await market.collect_kg_goszakupki(s, per_query=per_query)
    log.info("market.collected", **res)
    return res


# подсказка собственнику к ответу снабжения (prompts/answer_review); без ИИ задача не ставится
@broker.task(task_name="ai.review_answer", retry_on_error=True, timeout=120)
async def review_answer_task(finding_id: str) -> None:
    from secondkey.services import ai

    await ai.review_answer(uuid.UUID(finding_id))


@broker.task(task_name="registry.fetch_run", timeout=30 * 60)
async def registry_fetch_task(run_id: str) -> dict:
    """Сведения агрегатора по поставщикам прогона и пересчёт связок."""
    from secondkey.services.registry import fetch_for_run

    return await fetch_for_run(uuid.UUID(run_id))


@broker.task(task_name="integrations.sync", timeout=30 * 60)
async def sync_connection_task(connection_id: str, start: str | None = None, end: str | None = None) -> str | None:
    from datetime import date

    from secondkey.services import integrations

    run_id = await integrations.sync_connection(
        uuid.UUID(connection_id), date.fromisoformat(start) if start else None, date.fromisoformat(end) if end else None
    )
    return str(run_id) if run_id else None


# ежедневный забор из 1С для подключений с расписанием «daily»
@broker.task(task_name="integrations.sync_scheduled", schedule=[{"cron": "0 6 * * *", "cron_offset": "Asia/Bishkek"}])
async def sync_scheduled_task() -> int:
    from sqlalchemy import select

    from secondkey.db.models import Connection

    async with session_scope() as s:
        ids = [
            c.id
            for c in (await s.scalars(select(Connection).where(Connection.enabled.is_(True)))).all()
            if (c.config or {}).get("schedule") == "daily"
        ]
    for cid in ids:
        await sync_connection_task.kiq(str(cid))
    return len(ids)
