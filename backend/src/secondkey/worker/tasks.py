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
