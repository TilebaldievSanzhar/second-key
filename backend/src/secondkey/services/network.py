"""Сеть клиентов: почём тот же поставщик продаёт ту же позицию другим компаниям — только обезличенно.

Правила (docs/product/SUPPLIER_LINKS.md § 5):
- участвуют только организации, согласившиеся на обмен (settings.network_opt_in), — и видят сеть только они;
- по сочетанию «ИНН поставщика × позиция» нужно ≥ network_min_orgs других компаний и ≥ network_min_points покупок;
- отдаётся среднее из медиан по компаниям, округлённое до 10 сом: ни одну компанию по цене не узнать;
- только прошлые цены (последние network_window_days дней каждой проверки), без текущих предложений.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.config import settings
from secondkey.db.models import AuditRun, Organization, PurchaseLine, RunStatus


def opted_in(org: Organization | None) -> bool:
    return bool(org and (org.settings or {}).get("network_opt_in"))


async def stats_for(
    session: AsyncSession, org: Organization, pairs: set[tuple[str, str]]
) -> dict[tuple[str, str], dict]:
    if not opted_in(org) or not pairs:
        return {}
    others = [o.id for o in (await session.scalars(select(Organization))).all() if o.id != org.id and opted_in(o)]
    if len(others) < settings.network_min_orgs:
        return {}
    # последняя завершённая проверка каждой компании (сортировкой: max(дата) в SQLite — строка)
    runs: dict[uuid.UUID, AuditRun] = {}
    seen: set[uuid.UUID] = set()
    for run in (
        await session.scalars(
            select(AuditRun)
            .where(AuditRun.org_id.in_(others), AuditRun.status == RunStatus.done)
            .order_by(AuditRun.created_at.desc())
        )
    ).all():
        if run.org_id not in seen:
            seen.add(run.org_id)
            runs[run.id] = run
    if len(runs) < settings.network_min_orgs:
        return {}
    inns = {i for i, _ in pairs}
    skus = {k for _, k in pairs}
    rows = (
        await session.execute(
            select(
                PurchaseLine.run_id,
                PurchaseLine.supplier_inn,
                PurchaseLine.sku,
                PurchaseLine.price_base,
                PurchaseLine.date,
            ).where(
                PurchaseLine.run_id.in_(runs),
                PurchaseLine.supplier_inn.in_(inns),
                PurchaseLine.sku.in_(skus),
                PurchaseLine.price_base.is_not(None),
                PurchaseLine.norm_status == "ok",
            )
        )
    ).all()
    per: dict[tuple[str, str], dict[uuid.UUID, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    for run_id, inn, sku, price, day in rows:
        end = runs[run_id].period_end
        if end and day < end - timedelta(days=settings.network_window_days):
            continue
        per[(inn, sku)][runs[run_id].org_id].append(price)
    out = {}
    for key, by_org in per.items():
        if key not in pairs:
            continue
        points = sum(len(v) for v in by_org.values())
        if len(by_org) < settings.network_min_orgs or points < settings.network_min_points:
            continue
        medians = [Decimal(str(statistics.median(v))) for v in by_org.values()]
        mean = sum(medians, Decimal("0")) / len(medians)
        out[key] = {
            "price": (mean / 10).quantize(Decimal("1"), ROUND_HALF_UP) * 10,
            "orgs": len(by_org),
            "points": points,
        }
    return out
