"""Связки для интерфейса: матрица «снабженцы × поставщики», досье поставщика, «где дешевле».

Только чтение того, что посчитал прогон (purchase_lines, run_suppliers, findings, market_points) —
движок здесь не вызывается. Медианы — по цене за базовую единицу позиции (price_base).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import AuditRun, Finding, FindingLine, MarketPoint, PurchaseLine, RunSupplier

D0 = Decimal("0")
RECENT_DAYS = 90  # «где дешевле» — по последним трём месяцам проверки
ITEM_WINDOW_DAYS = 15  # цены других поставщиков — в те же месяцы, что покупки у этого (± полмесяца)


def _median(values: list[Decimal]) -> Decimal | None:
    return Decimal(str(statistics.median(values))).quantize(Decimal("0.01")) if values else None


def _groups(traces: list[dict]) -> list[str]:
    order = ["price", "behavior", "identity"]
    return sorted({t.get("group") for t in traces if t.get("group") in order}, key=order.index)


# --------------------------------------------------------------------------- матрица и список связок


async def links_overview(session: AsyncSession, run: AuditRun) -> dict[str, Any]:
    pairs = (
        await session.execute(
            select(PurchaseLine.supplier, PurchaseLine.responsible, func.sum(PurchaseLine.amount), func.count())
            .where(PurchaseLine.run_id == run.id)
            .group_by(PurchaseLine.supplier, PurchaseLine.responsible)
        )
    ).all()
    suppliers = (await session.scalars(select(RunSupplier).where(RunSupplier.run_id == run.id))).all()
    findings = (
        await session.scalars(
            select(Finding)
            .where(Finding.run_id == run.id, Finding.code == "LINK")
            .order_by(Finding.overpayment.desc(), Finding.created_at)
        )
    ).all()
    by_name = {s.name: s for s in suppliers}
    total_by_supplier: dict[str, Decimal] = defaultdict(lambda: D0)
    for supplier, _buyer, spend, _n in pairs:
        total_by_supplier[supplier] += spend

    linked: dict[str, set[str | None]] = defaultdict(set)  # поставщик → снабженцы связок (None — все)
    links = []
    for f in findings:
        ev = f.evidence or {}
        who = ev.get("responsible")
        linked[f.supplier or ""].add(who)
        sup = by_name.get(f.supplier or "")
        traces = list(ev.get("traces") or [])
        links.append(
            {
                "finding_id": f.id,
                "supplier": f.supplier,
                "supplier_id": sup.id if sup else None,
                "responsible": who,
                "status": f.status,
                "severity": f.severity,
                "overpayment": f.overpayment,
                "sku_label": f.sku_label,
                "pair_spend": Decimal(str(ev.get("spend") or 0)),
                "pair_share": Decimal(str(ev.get("pair_share") or 0)),
                "groups": _groups(traces),
                "traces": traces,
            }
        )

    cells = []
    buyers: dict[str | None, dict] = {}
    for supplier, buyer, spend, n in pairs:
        sup = by_name.get(supplier)
        traces = sup.traces if sup else []
        codes = sorted({t["code"] for t in traces if t.get("responsible") == buyer and buyer is not None})
        who_linked = linked.get(supplier, set())
        cells.append(
            {
                "supplier": supplier,
                "supplier_id": sup.id if sup else None,
                "buyer": buyer,
                "spend": spend,
                "lines": n,
                "share": (spend / total_by_supplier[supplier]) if total_by_supplier[supplier] else D0,
                "codes": codes,
                "linked": buyer in who_linked or None in who_linked,
            }
        )
        b = buyers.setdefault(buyer, {"name": buyer, "spend": D0, "lines": 0, "suppliers": 0, "links": 0})
        b["spend"] += spend
        b["lines"] += n
        b["suppliers"] += 1
    for link in links:
        if link["responsible"] in buyers:
            buyers[link["responsible"]]["links"] += 1

    rows = [
        {
            "id": s.id,
            "name": s.name,
            "spend": s.spend,
            "share": s.share,
            "inn_kind": s.inn_kind,
            "inn_date": s.inn_date,
            "linked": s.linked,
            "groups": _groups(s.traces),
            "traces": s.traces,
        }
        for s in sorted(suppliers, key=lambda s: -s.spend)
    ]
    return {
        "suppliers_total": len(suppliers),
        "buyers": sorted(buyers.values(), key=lambda b: -b["spend"]),
        "suppliers": rows,
        "cells": cells,
        "links": links,
    }


# --------------------------------------------------------------------------- досье поставщика


async def supplier_detail(session: AsyncSession, run: AuditRun, s: RunSupplier) -> dict[str, Any]:
    mine = (
        await session.scalars(
            select(PurchaseLine).where(
                PurchaseLine.run_id == run.id,
                PurchaseLine.supplier == s.name,
                PurchaseLine.sku.is_not(None),
                PurchaseLine.price_base.is_not(None),
            )
        )
    ).all()
    by_sku: dict[str, list[PurchaseLine]] = defaultdict(list)
    for r in mine:
        by_sku[r.sku].append(r)
    items = []
    for sku, rows in sorted(by_sku.items(), key=lambda kv: -sum((r.amount for r in kv[1]), D0))[:20]:
        start = min(r.date for r in rows) - timedelta(days=ITEM_WINDOW_DAYS)
        end = max(r.date for r in rows) + timedelta(days=ITEM_WINDOW_DAYS)
        others = (
            await session.execute(
                select(PurchaseLine.price_base, PurchaseLine.supplier).where(
                    PurchaseLine.run_id == run.id,
                    PurchaseLine.sku == sku,
                    PurchaseLine.supplier != s.name,
                    PurchaseLine.price_base.is_not(None),
                    PurchaseLine.date.between(start, end),
                )
            )
        ).all()
        market = (
            await session.scalars(
                select(MarketPoint.price).where(MarketPoint.sku == sku, MarketPoint.date.between(start, end))
            )
        ).all()
        price = _median([r.price_base for r in rows])
        other = _median([p for p, _ in others])
        mkt = _median(list(market))
        items.append(
            {
                "sku": sku,
                "label": rows[0].sku_label or sku,
                "unit": rows[0].unit_base,
                "lines": len(rows),
                "spend": sum((r.amount for r in rows), D0),
                "price": price,
                "others": other,
                "others_suppliers": len({name for _, name in others}),
                "market": mkt,
                "market_points": len(market),
                "vs_others": (price / other - 1) if price and other else None,
                "vs_market": (price / mkt - 1) if price and mkt else None,
            }
        )

    names = {
        r.name: r.id for r in (await session.scalars(select(RunSupplier).where(RunSupplier.run_id == run.id))).all()
    }
    shared = []
    for t in s.traces or []:
        if t.get("code") == "L-11":
            other = (t.get("facts") or {}).get("other")
            shared.append(
                {"other": other, "other_id": names.get(other), "fields": (t.get("facts") or {}).get("fields", [])}
            )
    return {"items": items, "shared": shared}


# --------------------------------------------------------------------------- где дешевле


async def alternatives(session: AsyncSession, run: AuditRun, f: Finding) -> dict[str, Any]:
    """По главным позициям вопроса: кто продаёт дешевле — по вашей истории и по рынку (госзакупки)."""
    rows = (
        await session.scalars(
            select(PurchaseLine)
            .join(FindingLine, FindingLine.line_id == PurchaseLine.id)
            .where(FindingLine.finding_id == f.id, PurchaseLine.sku.is_not(None), PurchaseLine.price_base.is_not(None))
        )
    ).all()
    spend: dict[str, Decimal] = defaultdict(lambda: D0)
    for r in rows:
        spend[r.sku] += r.amount
    skus = sorted(spend, key=lambda k: -spend[k])[:3]
    end = run.period_end or max((r.date for r in rows), default=date.today())
    start = end - timedelta(days=RECENT_DAYS)
    groups = []
    for sku in skus:
        lines = (
            await session.scalars(
                select(PurchaseLine)
                .where(PurchaseLine.run_id == run.id, PurchaseLine.sku == sku, PurchaseLine.price_base.is_not(None))
                .order_by(PurchaseLine.date)
            )
        ).all()
        by_supplier: dict[str, list[PurchaseLine]] = defaultdict(list)
        for ln in lines:
            by_supplier[ln.supplier].append(ln)

        def quote(items: list[PurchaseLine]) -> dict[str, Any]:
            recent = [x for x in items if x.date >= start]
            use = recent or items[-3:]  # давно не покупали — последние три цены, с датой
            return {
                "price": _median([x.price_base for x in use]),
                "lines": len(use),
                "last_date": items[-1].date,
                "recent": bool(recent),
            }

        sample = next(r for r in rows if r.sku == sku)
        current_lines = by_supplier.get(f.supplier or "", [])
        current = quote(current_lines) if current_lines else None
        options = []
        for name, items in by_supplier.items():
            if name == f.supplier:
                continue
            q = quote(items)
            delta = (q["price"] / current["price"] - 1) if current and current["price"] and q["price"] else None
            options.append({"supplier": name, "delta": delta, **q})
        options.sort(key=lambda o: o["price"] or Decimal("Infinity"))
        points = (
            await session.scalars(select(MarketPoint).where(MarketPoint.sku == sku).order_by(MarketPoint.date))
        ).all()
        market = None
        if points:
            recent = [p for p in points if p.date >= start]
            use = recent or points[-5:]
            mp = _median([p.price for p in use])
            market = {
                "price": mp,
                "points": len(use),
                "last_date": points[-1].date,
                "recent": bool(recent),
                "delta": (mp / current["price"] - 1) if current and current["price"] and mp else None,
            }
        groups.append(
            {
                "sku": sku,
                "label": sample.sku_label or sku,
                "unit": sample.unit_base,
                "supplier": f.supplier,
                "current": current,
                "options": options[:5],
                "market": market,
            }
        )
    return {"window_start": start, "window_end": end, "groups": groups}
