"""HTML-отчёт аудита закупок для собственника.

Все суммы и доли считаются здесь кодом из AuditResult; шаблон только раскладывает.
Каждая находка раскрывается до строк учёта (дата, документ, номенклатура как
записана, цена, эталон) — отчёт должен выдерживать вопрос «откуда эта цифра».
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from secondkey.engine.models import AuditResult, doc_short

TEMPLATES = Path(__file__).parent / "templates"
UNIT_RU = {
    "t": "т",
    "kg": "кг",
    "m3": "м³",
    "m2": "м²",
    "m": "м",
    "pcs": "шт",
    "l": "л",
    "sheet": "лист",
    "bag": "мешок",
    "pack": "упак",
}
RULE_TITLES = {
    "P-01": "Дороже параллельного поставщика",
    "P-02": "Ползучий рост цены",
    "P-03": "Выше рынка",
    "P-04": "Концентрация у дорогого поставщика",
    "P-05": "Дробление закупки",
    "P-06": "Повтор документа",
    "P-07": "Ошибка единицы в учёте",
}
MONEY_RULES = ("P-01", "P-02", "P-03", "P-06")


def money(v: Decimal | int | float | None) -> str:
    if v is None:
        return "—"
    v = Decimal(v).quantize(Decimal("1"), ROUND_HALF_UP)
    return f"{v:,}".replace(",", " ")


def qty(v: Decimal | None) -> str:
    if v is None:
        return "—"
    s = format(Decimal(v).normalize(), "f")
    whole, _, frac = s.partition(".")
    whole = f"{int(whole):,}".replace(",", " ") if whole.lstrip("-").isdigit() else whole
    return whole + ("," + frac if frac else "")


def pct(v: Decimal | float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    q = Decimal(1).scaleb(-digits)
    return f"{(Decimal(v) * 100).quantize(q, ROUND_HALF_UP)}%".replace(".", ",")


def build_context(res: AuditResult, market_points: int = 0) -> dict:
    by_id = {nl.line_id: nl for nl in res.lines}
    spend = res.total_spend
    over = res.total_overpayment

    # чьё правило дало максимум по строке — туда и относим деньги (без двойного счёта)
    owner_rule: dict[str, str] = {}
    best: dict[str, Decimal] = {}
    for f in res.findings:
        for lid, v in f.line_overpayment.items():
            if v > best.get(lid, Decimal("0")):
                best[lid], owner_rule[lid] = v, f.code

    by_rule = defaultdict(lambda: {"money": Decimal("0"), "count": 0})
    for f in res.findings:
        by_rule[f.code]["count"] += 1
    for lid, v in res.line_overpayment.items():
        by_rule[owner_rule[lid]]["money"] += v

    def breakdown(key) -> list[dict]:
        sp, ov, n = defaultdict(lambda: Decimal("0")), defaultdict(lambda: Decimal("0")), defaultdict(int)
        for nl in res.lines:
            k = key(nl) or "не указано"
            sp[k] += nl.line.amount
            v = res.line_overpayment.get(nl.line_id)
            if v:
                ov[k] += v
                n[k] += 1
        rows = [
            {"name": k, "spend": sp[k], "over": ov[k], "share": (ov[k] / sp[k]) if sp[k] else None, "lines": n[k]}
            for k in sp
            if ov[k] > 0
        ]
        return sorted(rows, key=lambda r: -r["over"])

    def evidence(f) -> list[dict]:
        rows = []
        for lid in f.line_ids:
            nl = by_id.get(lid)
            if nl is None:
                continue
            L = nl.line
            rows.append(
                {
                    "date": L.date.strftime("%d.%m.%Y"),
                    "doc": doc_short(L.doc_no),
                    "row": L.source_row,
                    "object": L.object_name or "—",
                    "who": L.responsible or "—",
                    "supplier": L.supplier,
                    "item": L.item_raw,
                    "qty": L.qty,
                    "unit": L.unit_raw or "",
                    "price": L.price,
                    "price_base": nl.price,
                    "unit_base": UNIT_RU.get(nl.unit or "", nl.unit or ""),
                    "over": f.line_overpayment.get(lid),
                }
            )
        rows.sort(key=lambda r: -(r["over"] or 0))
        return rows

    money_findings = [f for f in res.findings if f.code in MONEY_RULES and f.overpayment > 0]
    signals = [f for f in res.findings if f.code not in MONEY_RULES or f.overpayment == 0]
    needs = [nl for nl in res.lines if nl.status == "needs_input"]
    ok = sum(1 for nl in res.lines if nl.status == "ok")

    return {
        "company": res.company,
        "period": res.period,
        "generated": date.today(),
        "lines": len(res.lines),
        "ok_lines": ok,
        "ok_share": (Decimal(ok) / len(res.lines)) if res.lines else None,
        "spend": spend,
        "over": over,
        "over_share": (over / spend) if spend else None,
        "suppliers": len({nl.line.supplier for nl in res.lines}),
        "skus": len({nl.sku for nl in res.lines if nl.sku}),
        "market_points": market_points,
        "rules": [{"code": c, "title": RULE_TITLES[c], **by_rule[c]} for c in sorted(by_rule)],
        "by_supplier": breakdown(lambda nl: nl.line.supplier)[:12],
        "by_who": breakdown(lambda nl: nl.line.responsible),
        "by_head": breakdown(lambda nl: (nl.head or "").capitalize() or None)[:12],
        "findings": [{"f": f, "rows": evidence(f)} for f in money_findings],
        "signals": [{"f": f, "rows": evidence(f)[:12]} for f in signals],
        "needs": needs[:40],
        "needs_count": len(needs),
        "warnings": res.warnings[:30],
        "warnings_count": len(res.warnings),
        "unit_ru": UNIT_RU,
    }


def render_html(res: AuditResult, market_points: int = 0) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["money"] = money
    env.filters["pct"] = pct
    env.filters["qty"] = qty
    return env.get_template("audit_report.html").render(**build_context(res, market_points))


def render_report(res: AuditResult, out_path: str | Path, market_points: int = 0) -> Path:
    html = render_html(res, market_points)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
