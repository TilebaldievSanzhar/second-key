"""Правила аудита закупок P-01…P-07. Только детерминированный код.

Каждое правило возвращает findings с перечнем строк и вкладом каждой строки в
переплату. Одна строка может попасть под несколько правил — в итог идёт максимум
по строке (без двойного счёта), это делает pipeline.

P-01 дороже параллельного поставщика   (цена − эталон) × кол-во, эталон — дешёвый поставщик в том же окне
P-02 ползучий рост цены                 разница против цены, растущей вместе с эталоном
P-03 выше рынка                         (цена − эталон × (1 + допуск)) × кол-во — оценка снизу
P-04 концентрация у дорогого поставщика структурный сигнал
P-05 дробление закупки                  процессный сигнал
P-06 дубль документа                    сумма дубля
P-07 ошибка единицы                     ошибка учёта, строка исключается из ценовых правил
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from secondkey.engine.models import AuditFinding, NormLine, doc_short

D0 = Decimal("0")


@dataclass
class AuditConfig:
    parallel_window_days: int = 30
    parallel_nearest: int = 2
    parallel_tolerance: Decimal = Decimal("0.05")
    creep_min_lines: int = 4
    creep_min_span_days: int = 90
    creep_threshold: Decimal = Decimal("0.08")
    market_window_days: int = 60
    market_min_points: int = 3
    market_tolerance: Decimal = Decimal("0.10")
    concentration_share: Decimal = Decimal("0.6")
    concentration_premium: Decimal = Decimal("0.05")
    split_window_days: int = 7
    split_min_lines: int = 3
    split_near_threshold: Decimal = Decimal("0.8")  # каждая покупка ≥ 80% порога: «чуть ниже порога»
    approval_threshold: Decimal = Decimal("500000")
    unit_error_factor: Decimal = Decimal("8")


# поставщик рынка: (sku, дата, окно в днях) → цены за базовую единицу
MarketLookup = Callable[[str, "object", int], list[Decimal]]


def _median(values: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(values)))


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("1"), ROUND_HALF_UP)


def _fmt(v: Decimal) -> str:
    return f"{_money(v):,}".replace(",", " ")


def _pct(v: Decimal) -> str:
    return f"{(v * 100).quantize(Decimal('0.1'))}%".replace(".", ",")


def _groups(lines: list[NormLine]) -> dict[str, list[NormLine]]:
    g: dict[str, list[NormLine]] = defaultdict(list)
    for nl in lines:
        if nl.status == "ok" and nl.sku and nl.price is not None:
            g[nl.sku].append(nl)
    for v in g.values():
        v.sort(key=lambda n: (n.line.date, n.line.line_id))
    return g


def _in_window(a: NormLine, b: NormLine, days: int) -> bool:
    return abs((a.line.date - b.line.date).days) <= days


# --------------------------------------------------------------------------- P-07


def p07_unit_errors(lines: list[NormLine], cfg: AuditConfig) -> list[AuditFinding]:
    """Цена за базовую единицу в разы отличается от медианы позиции — ошибка единицы/учёта.

    Помечает строку status=unit_error: дальше ценовые правила её не видят
    (иначе ошибка учёта превратилась бы в «переплату» на миллионы).
    """
    out = []
    for sku, grp in _groups(lines).items():
        if len(grp) < 4:
            continue
        med = _median([n.price for n in grp])
        if med <= 0:
            continue
        for n in grp:
            ratio = n.price / med
            if ratio > cfg.unit_error_factor or ratio < 1 / cfg.unit_error_factor:
                n.status = "unit_error"
                n.note = f"цена {_fmt(n.price)} при медиане позиции {_fmt(med)} (×{ratio.quantize(Decimal('0.01'))})"
                out.append(
                    AuditFinding(
                        code="P-07",
                        severity="info",
                        title="Ошибка единицы или цены в учёте",
                        message=(
                            f"{n.sku_label}: цена {_fmt(n.price)} сом за {n.unit} отличается от обычной "
                            f"({_fmt(med)}) в {ratio.quantize(Decimal('0.1'))} раза — вероятно, перепутана единица"
                        ),
                        line_ids=[n.line_id],
                        sku=sku,
                        sku_label=n.sku_label,
                        supplier=n.line.supplier,
                        reference=med,
                        reference_kind="unit",
                        evidence={"ratio": str(ratio.quantize(Decimal("0.001")))},
                    )
                )
    return out


# --------------------------------------------------------------------------- P-01


def p01_parallel_supplier(lines: list[NormLine], cfg: AuditConfig) -> list[AuditFinding]:
    acc: dict[tuple[str, str], dict] = {}
    for sku, grp in _groups(lines).items():
        if len({n.line.supplier for n in grp}) < 2:
            continue
        for n in grp:
            others: dict[str, list[tuple[int, Decimal]]] = defaultdict(list)
            for o in grp:
                if o.line.supplier != n.line.supplier and _in_window(n, o, cfg.parallel_window_days):
                    others[o.line.supplier].append((abs((o.line.date - n.line.date).days), o.price))
            if not others:
                continue
            # эталон поставщика — его ближайшие по времени покупки: скачок рынка внутри окна
            # (сезон, дефицит) не делает «переплатой» покупку по новой цене
            refs = {s: _median([p for _, p in sorted(v)[: cfg.parallel_nearest]]) for s, v in others.items()}
            ref_supplier, ref = min(refs.items(), key=lambda x: x[1])
            if n.price > ref * (1 + cfg.parallel_tolerance):
                over = (n.price - ref) * n.qty
                a = acc.setdefault(
                    (sku, n.line.supplier), {"lines": {}, "refs": [], "alts": set(), "nl": n, "prem": []}
                )
                a["lines"][n.line_id] = over
                a["refs"].append(ref)
                a["alts"].add(ref_supplier)
                a["prem"].append(n.price / ref - 1)
    out = []
    for (sku, supplier), a in acc.items():
        total = sum(a["lines"].values(), D0)
        prem = _median(a["prem"])
        nl = a["nl"]
        alts = ", ".join(sorted(a["alts"]))
        out.append(
            AuditFinding(
                code="P-01",
                severity="high" if prem >= Decimal("0.10") else "medium",
                title="Дороже параллельного поставщика",
                message=(
                    f"{nl.sku_label}: у «{supplier}» в среднем на {_pct(prem)} дороже, чем в те же недели "
                    f"у {alts}; покупок: {len(a['lines'])}, переплата {_fmt(total)} сом"
                ),
                line_ids=list(a["lines"]),
                overpayment=_money(total),
                sku=sku,
                sku_label=nl.sku_label,
                supplier=supplier,
                reference=_median(a["refs"]),
                reference_kind="parallel",
                evidence={
                    "premium_median": str(prem.quantize(Decimal("0.001"))),
                    "cheaper_suppliers": sorted(a["alts"]),
                },
                line_overpayment={k: _money(v) for k, v in a["lines"].items()},
            )
        )
    return out


# --------------------------------------------------------------------------- P-02


def _reference_at(n: NormLine, grp: list[NormLine], market: MarketLookup | None, cfg: AuditConfig) -> Decimal | None:
    others = [o.price for o in grp if o.line.supplier != n.line.supplier and _in_window(n, o, cfg.parallel_window_days)]
    if others:
        return _median(others)
    if market is not None:
        pts = market(n.sku, n.line.date, cfg.market_window_days)
        if len(pts) >= cfg.market_min_points:
            return _median(pts)
    return None


def p02_price_creep(lines: list[NormLine], cfg: AuditConfig, market: MarketLookup | None = None) -> list[AuditFinding]:
    out = []
    for sku, grp in _groups(lines).items():
        by_sup: dict[str, list[NormLine]] = defaultdict(list)
        for n in grp:
            by_sup[n.line.supplier].append(n)
        for supplier, sl in by_sup.items():
            if len(sl) < cfg.creep_min_lines or (sl[-1].line.date - sl[0].line.date).days < cfg.creep_min_span_days:
                continue
            ratios = []
            for n in sl:
                r = _reference_at(n, grp, market, cfg)
                if r:
                    ratios.append((n, r, n.price / r))
            if len(ratios) < cfg.creep_min_lines:
                continue
            third = max(1, len(ratios) // 3)
            start = _median([x[2] for x in ratios[:third]])
            end = _median([x[2] for x in ratios[-third:]])
            if end - start < cfg.creep_threshold or end < 1 + cfg.parallel_tolerance:
                continue
            base_ratio = max(start, Decimal("1"))
            contrib = {}
            for n, r, _ratio in ratios[third:]:
                fair = r * base_ratio
                if n.price > fair:
                    contrib[n.line_id] = _money((n.price - fair) * n.qty)
            if not contrib:
                continue
            total = sum(contrib.values(), D0)
            nl = sl[0]
            out.append(
                AuditFinding(
                    code="P-02",
                    severity="high",
                    title="Ползучий рост цены",
                    message=(
                        f"{nl.sku_label}: у «{supplier}» цена относительно рынка выросла с {_pct(start - 1)} "
                        f"до {_pct(end - 1)} за {(sl[-1].line.date - sl[0].line.date).days} дн.; "
                        f"переплата против роста вместе с рынком {_fmt(total)} сом"
                    ),
                    line_ids=list(contrib),
                    overpayment=total,
                    sku=sku,
                    sku_label=nl.sku_label,
                    supplier=supplier,
                    reference=None,
                    reference_kind="creep",
                    evidence={
                        "ratio_start": str(start.quantize(Decimal("0.001"))),
                        "ratio_end": str(end.quantize(Decimal("0.001"))),
                    },
                    line_overpayment=contrib,
                )
            )
    return out


# --------------------------------------------------------------------------- P-03


def p03_above_market(lines: list[NormLine], cfg: AuditConfig, market: MarketLookup | None) -> list[AuditFinding]:
    if market is None:
        return []
    acc: dict[tuple[str, str], dict] = {}
    for sku, grp in _groups(lines).items():
        for n in grp:
            pts = market(sku, n.line.date, cfg.market_window_days)
            if len(pts) < cfg.market_min_points:
                continue
            ref = _median(pts)
            limit = ref * (1 + cfg.market_tolerance)
            if n.price > limit:
                a = acc.setdefault((sku, n.line.supplier), {"lines": {}, "refs": [], "nl": n, "prem": [], "pts": 0})
                a["lines"][n.line_id] = _money((n.price - limit) * n.qty)
                a["refs"].append(ref)
                a["prem"].append(n.price / ref - 1)
                a["pts"] = max(a["pts"], len(pts))
    out = []
    for (sku, supplier), a in acc.items():
        total = sum(a["lines"].values(), D0)
        prem = _median(a["prem"])
        nl = a["nl"]
        out.append(
            AuditFinding(
                code="P-03",
                severity="medium",
                title="Выше рынка",
                message=(
                    f"{nl.sku_label}: у «{supplier}» на {_pct(prem)} выше рыночной цены "
                    f"(медиана {_fmt(_median(a['refs']))} сом за {nl.unit}); покупок: {len(a['lines'])}; "
                    f"переплата сверх допуска {_pct(cfg.market_tolerance)} — {_fmt(total)} сом"
                ),
                line_ids=list(a["lines"]),
                overpayment=total,
                sku=sku,
                sku_label=nl.sku_label,
                supplier=supplier,
                reference=_median(a["refs"]),
                reference_kind="market",
                evidence={"premium_median": str(prem.quantize(Decimal("0.001"))), "market_points_max": a["pts"]},
                line_overpayment=a["lines"],
            )
        )
    return out


# --------------------------------------------------------------------------- P-04


def p04_concentration(lines: list[NormLine], cfg: AuditConfig) -> list[AuditFinding]:
    groups = _groups(lines)
    by_head: dict[str, list[NormLine]] = defaultdict(list)
    for grp in groups.values():
        for n in grp:
            by_head[n.head].append(n)
    out = []
    for head, hl in by_head.items():
        spend: dict[str, Decimal] = defaultdict(lambda: D0)
        for n in hl:
            spend[n.line.supplier] += n.line.amount
        total = sum(spend.values(), D0)
        if len(spend) < 2 or total <= 0:
            continue
        top, top_spend = max(spend.items(), key=lambda x: x[1])
        share = top_spend / total
        if share < cfg.concentration_share:
            continue
        prem = []
        for n in hl:
            if n.line.supplier != top:
                continue
            others = [
                o.price for o in groups[n.sku] if o.line.supplier != top and _in_window(n, o, cfg.parallel_window_days)
            ]
            if others:
                prem.append(n.price / _median(others) - 1)
        if len(prem) < 3:
            continue
        p = _median(prem)
        if p < cfg.concentration_premium:
            continue
        out.append(
            AuditFinding(
                code="P-04",
                severity="medium",
                title="Концентрация у дорогого поставщика",
                message=(
                    f"{head.capitalize()}: «{top}» получает {_pct(share)} закупок категории и при этом "
                    f"в среднем на {_pct(p)} дороже других поставщиков в те же недели"
                ),
                line_ids=[n.line_id for n in hl if n.line.supplier == top],
                sku=None,
                sku_label=head.capitalize(),
                supplier=top,
                reference_kind="structure",
                evidence={
                    "share": str(share.quantize(Decimal("0.001"))),
                    "premium_median": str(p.quantize(Decimal("0.001"))),
                },
            )
        )
    return out


# --------------------------------------------------------------------------- P-05


def p05_split(lines: list[NormLine], cfg: AuditConfig) -> list[AuditFinding]:
    out = []
    by_key: dict[tuple[str, str, str], list[NormLine]] = defaultdict(list)
    for grp in _groups(lines).values():
        for n in grp:
            by_key[(n.line.supplier, n.sku, n.line.object_name or "")].append(n)
    low = cfg.approval_threshold * cfg.split_near_threshold
    for (supplier, sku, _obj), sl in by_key.items():
        small = [n for n in sl if low <= n.line.amount < cfg.approval_threshold]
        i = 0
        while i < len(small):
            cluster = [small[i]]
            j = i + 1
            while j < len(small) and (small[j].line.date - small[i].line.date).days <= cfg.split_window_days:
                cluster.append(small[j])
                j += 1
            s = sum((n.line.amount for n in cluster), D0)
            if len(cluster) >= cfg.split_min_lines and s >= cfg.approval_threshold:
                nl = cluster[0]
                out.append(
                    AuditFinding(
                        code="P-05",
                        severity="medium",
                        title="Дробление закупки",
                        message=(
                            f"{nl.sku_label}: {len(cluster)} покупок у «{supplier}» за "
                            f"{(cluster[-1].line.date - cluster[0].line.date).days + 1} дн., каждая чуть ниже порога "
                            f"согласования {_fmt(cfg.approval_threshold)} сом, вместе {_fmt(s)} сом"
                        ),
                        line_ids=[n.line_id for n in cluster],
                        sku=sku,
                        sku_label=nl.sku_label,
                        supplier=supplier,
                        reference_kind="process",
                        evidence={"sum": str(_money(s))},
                    )
                )
                i = j
            else:
                i += 1
    return out


# --------------------------------------------------------------------------- P-06


def p06_duplicates(lines: list[NormLine], cfg: AuditConfig) -> list[AuditFinding]:
    seen: dict[tuple, NormLine] = {}
    out = []
    for n in sorted(lines, key=lambda x: (x.line.date, x.line.source_row or 0, x.line.line_id)):
        L = n.line
        key = (
            L.supplier.lower(),
            (L.doc_no or "").lower() or L.date.isoformat(),
            L.item_raw.strip().lower(),
            L.qty,
            L.amount,
        )
        if key in seen:
            first = seen[key]
            out.append(
                AuditFinding(
                    code="P-06",
                    severity="high",
                    title="Повтор документа",
                    message=(
                        f"«{L.supplier}», документ {doc_short(L.doc_no)} от {L.date:%d.%m.%Y}: "
                        f"строка «{L.item_raw}» на {_fmt(L.amount)} сом учтена повторно"
                    ),
                    line_ids=[first.line_id, n.line_id],
                    overpayment=_money(L.amount),
                    sku=n.sku,
                    sku_label=n.sku_label or L.item_raw,
                    supplier=L.supplier,
                    reference_kind="duplicate",
                    evidence={"first_line": first.line_id},
                    line_overpayment={n.line_id: _money(L.amount)},
                )
            )
        else:
            seen[key] = n
    return out


def run_all(
    lines: list[NormLine], cfg: AuditConfig | None = None, market: MarketLookup | None = None
) -> list[AuditFinding]:
    cfg = cfg or AuditConfig()
    findings = p06_duplicates(lines, cfg)
    findings += p07_unit_errors(lines, cfg)  # помечает строки — до ценовых правил
    findings += p01_parallel_supplier(lines, cfg)
    findings += p02_price_creep(lines, cfg, market)
    findings += p03_above_market(lines, cfg, market)
    findings += p04_concentration(lines, cfg)
    findings += p05_split(lines, cfg)
    return findings
