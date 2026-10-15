"""Эталон рынка и курсы: сбор из внешних источников и сохранение в БД без дублей."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import FxRate, MarketPoint
from secondkey.engine.market import MarketPoint as EnginePoint
from secondkey.engine.models import PurchaseLine
from secondkey.engine.normalize import normalize_line
from secondkey.market import kg_goszakupki
from secondkey.refdata import fx_kg


def _insert(session: AsyncSession, model):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


async def save_points(session: AsyncSession, points: list[EnginePoint]) -> int:
    """Upsert точек эталона; повторный сбор не плодит дубли. Возвращает число новых точек."""
    if not points:
        return 0
    rows = [
        {
            "sku": p.sku,
            "date": p.date,
            "price": p.price,
            "unit": p.unit,
            "source": p.source,
            "region": p.region,
            "ref": p.ref,
            "label": p.label,
        }
        for p in points
    ]
    added = 0
    for i in range(0, len(rows), 1000):
        stmt = _insert(session, MarketPoint).values(rows[i : i + 1000]).on_conflict_do_nothing()
        added += (await session.execute(stmt)).rowcount or 0
    return added


def points_from_raw(records: list[dict[str, Any]]) -> list[EnginePoint]:
    """Сырые цены «название, единица, цена» → точки позиций тем же нормализатором, что и строки клиента."""
    out = []
    for r in records:
        price = Decimal(str(r["price"]))
        pl = PurchaseLine(
            line_id="m",
            date=date.fromisoformat(r["date"]),
            supplier="market",
            item_raw=r["name"],
            qty=Decimal("1"),
            unit_raw=r.get("unit"),
            price=price,
            amount=price,
        )
        nl = normalize_line(pl)
        if nl.status == "ok" and nl.sku:
            out.append(
                EnginePoint(
                    sku=nl.sku,
                    date=pl.date,
                    price=nl.price,
                    unit=nl.unit,
                    source=r["source"],
                    region=r.get("region"),
                    ref=r.get("ref"),
                    label=nl.sku_label,
                )
            )
    return out


async def collect_kg_goszakupki(
    session: AsyncSession, queries: list[str] | None = None, per_query: int = 40
) -> dict[str, int]:
    """Портал госзакупок КР → эталон. Сеть и разбор — в потоке: клиент синхронный и вежливый (паузы)."""
    records = await asyncio.to_thread(kg_goszakupki.collect, queries, per_query)
    points, skipped = kg_goszakupki.records_to_points([r.__dict__ for r in records])
    added = await save_points(session, points)
    return {"records": len(records), "points": len(points), "added": added, "skipped": skipped}


async def fetch_fx(session: AsyncSession) -> dict[str, Any]:
    snap = await asyncio.to_thread(fx_kg.fetch_rates)
    on = date.fromisoformat(snap["published"])
    rows = [
        {"date": on, "currency": cur, "rate": Decimal(rate), "source": "nbkr"} for cur, rate in snap["rates"].items()
    ]
    if rows:
        stmt = _insert(session, FxRate).values(rows)
        stmt = stmt.on_conflict_do_update(index_elements=["date", "currency"], set_={"rate": stmt.excluded.rate})
        await session.execute(stmt)
    return {"date": snap["published"], "currencies": len(rows)}
