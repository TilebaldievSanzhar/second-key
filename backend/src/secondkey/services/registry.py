"""Сведения реестра о поставщиках: ручная проверка организации и общий кэш агрегатора.

Приоритет — ручная проверка этой организации (человек смотрел выписку Минюста), затем свежая запись агрегатора.
Записи агрегатора — публичные сведения о юрлицах, общие для всех клиентов (org_id пусто); ручные — только своей
организации.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.config import settings
from secondkey.db.models import AuditRun, RegistryRecordRow, RunSupplier
from secondkey.db.session import session_scope
from secondkey.engine.registry import RegistryRecord
from secondkey.observability import log


async def records_for(session: AsyncSession, org_id: uuid.UUID, inns: set[str]) -> dict[str, RegistryRecord]:
    inns = {i for i in inns if i}
    if not inns:
        return {}
    rows = (
        await session.scalars(
            select(RegistryRecordRow).where(
                RegistryRecordRow.inn.in_(inns),
                or_(RegistryRecordRow.org_id == org_id, RegistryRecordRow.org_id.is_(None)),
            )
        )
    ).all()
    best: dict[str, RegistryRecordRow] = {}
    for r in rows:
        cur = best.get(r.inn)
        rank = (r.org_id is not None, r.checked_at)  # своя ручная проверка важнее, затем свежесть
        if cur is None or rank > (cur.org_id is not None, cur.checked_at):
            best[r.inn] = r
    return {inn: RegistryRecord.from_dict({**r.data, "inn": inn, "source": r.source}) for inn, r in best.items()}


async def save_manual(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, inn: str, fields: dict
) -> RegistryRecord:
    """Ручная проверка по выписке: одна запись на ИНН у организации, повторное сохранение её обновляет."""
    rec = RegistryRecord.from_dict(
        {**fields, "inn": inn, "source": "manual", "checked_at": fields.get("checked_at") or date.today()}
    )
    row = await session.scalar(
        select(RegistryRecordRow).where(
            RegistryRecordRow.inn == inn, RegistryRecordRow.org_id == org_id, RegistryRecordRow.source == "manual"
        )
    )
    if row is None:
        row = RegistryRecordRow(inn=inn, org_id=org_id, source="manual", created_by=user_id)
        session.add(row)
    row.data = rec.as_dict()
    row.checked_at = datetime.now(UTC)
    row.created_by = user_id
    await session.flush()
    return rec


async def fetch_for_run(run_id: uuid.UUID, provider=None) -> dict:
    """Запросить у агрегатора поставщиков прогона без свежих сведений в кэше, затем пересчитать связки."""
    from secondkey.registries import RegistryError
    from secondkey.registries import provider as make_provider
    from secondkey.services.links_refresh import refresh_links

    prov = provider or make_provider()
    if prov is None:
        return {"fetched": 0, "skipped": "агрегатор не подключён"}
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        if run is None:
            return {"fetched": 0}
        sups = (await s.scalars(select(RunSupplier).where(RunSupplier.run_id == run_id))).all()
        # сначала поставщики со следами, потом крупные: лимит запросов — деньги по тарифу
        order = sorted(sups, key=lambda x: (not x.linked, not x.traces, -x.spend))
        inns = [x.inn for x in order if x.inn][: settings.registry_fetch_limit]
        fresh_after = datetime.now(UTC) - timedelta(days=settings.registry_ttl_days)
        cached = {
            r.inn
            for r in (
                await s.scalars(
                    select(RegistryRecordRow).where(
                        RegistryRecordRow.inn.in_(inns),
                        RegistryRecordRow.org_id.is_(None),
                        RegistryRecordRow.source == prov.name,
                        RegistryRecordRow.checked_at >= fresh_after,
                    )
                )
            ).all()
        }
    todo = [i for i in inns if i not in cached]
    fetched, missing, errors = 0, 0, []
    try:
        for inn in todo:
            try:
                rec = await prov.lookup(inn)
            except RegistryError as exc:
                errors.append(str(exc))
                if len(errors) >= 3:  # ключ или лимит — дальше нет смысла
                    break
                continue
            if rec is None:
                missing += 1
                continue
            async with session_scope() as s:
                row = await s.scalar(
                    select(RegistryRecordRow).where(
                        RegistryRecordRow.inn == inn,
                        RegistryRecordRow.org_id.is_(None),
                        RegistryRecordRow.source == prov.name,
                    )
                )
                if row is None:
                    row = RegistryRecordRow(inn=inn, org_id=None, source=prov.name)
                    s.add(row)
                row.data, row.checked_at = rec.as_dict(), datetime.now(UTC)
            fetched += 1
    finally:
        await prov.aclose()
    refreshed = await refresh_links(run_id)
    log.info("registry.fetched", run_id=str(run_id), fetched=fetched, missing=missing, errors=len(errors))
    return {"fetched": fetched, "cached": len(cached), "missing": missing, "errors": errors[:3], **refreshed}
