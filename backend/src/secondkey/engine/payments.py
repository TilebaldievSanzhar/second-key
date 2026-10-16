"""Оплаты поставщикам: предоплата, скорость оплаты, аванс без поставки. Только детерминированный код.

Поставки — строки закупок (сумма с НДС), оплаты — списания с расчётного счёта и из кассы с видом «оплата
поставщику». По каждому поставщику события идут по времени: поставка гасит ранние оплаты (предоплата), оплата
гасит ранние поставки (постоплата, с задержкой в днях). Превышение оплат над поставками — аванс.

  P-09  аванс без поставки: оплачено больше, чем поставлено, и превышение держится 30+ дней — деньги под риском
  L-07  след связки (поведение): аванс без поставки; почти всё — по предоплате, когда остальным платят после
        поставки; оплата вне очереди — через день-два, когда остальным через недели

Аванс — не переплата: в итог «возможной переплаты» он не идёт, сумма — в evidence находки.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from secondkey.engine.models import AuditFinding, NormLine, Trace
from secondkey.engine.rules import AuditConfig, _fmt, _money, _pct, _q

D0 = Decimal("0")


@dataclass
class Payment:
    """Оплата поставщику: списание с расчётного счёта или расходный кассовый ордер."""

    date: date
    supplier: str
    amount: Decimal  # с НДС, как в документе
    supplier_inn: str | None = None
    doc_no: str | None = None
    purpose: str | None = None
    payment_id: str = ""  # «p12» — номер записи в выгрузке


@dataclass
class PaymentProfile:
    """Как платят поставщику за период."""

    supplier: str
    paid: Decimal
    delivered: Decimal
    payments: int
    prepaid: Decimal  # сколько поставок покрыто оплатой заранее
    postpaid: Decimal  # сколько поставок оплачено после
    delay_days: Decimal | None  # средневзвешенная задержка оплаты после поставки
    advance: Decimal  # превышение оплат над поставками на конец периода
    advance_since: date | None  # с какой даты превышение не опускалось ниже порога
    unpaid: Decimal  # поставлено, но не оплачено на конец периода
    # хронология с даты, когда возник аванс: оплаты и сколько за это время поставлено — доказательство
    since_payments: list[dict] = field(default_factory=list)
    since_paid: Decimal = D0
    since_delivered: Decimal = D0

    @property
    def prepay_share(self) -> Decimal | None:
        return self.prepaid / self.delivered if self.delivered else None

    def as_dict(self) -> dict:
        return {
            "paid": str(_money(self.paid)),
            "delivered": str(_money(self.delivered)),
            "payments": self.payments,
            "prepay_share": str(self.prepay_share.quantize(Decimal("0.001")))
            if self.prepay_share is not None
            else None,
            "delay_days": str(self.delay_days.quantize(Decimal("0.1"))) if self.delay_days is not None else None,
            "advance": str(_money(self.advance)),
            "advance_since": self.advance_since.isoformat() if self.advance_since else None,
            "unpaid": str(_money(self.unpaid)),
        }


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[«»\"'“”]", " ", name.lower())).strip()


def match_suppliers(lines: list[NormLine], payments: list[Payment]) -> dict[str, list[Payment]]:
    """Оплаты → поставщик из строк закупок: по ИНН, иначе по наименованию; чужие (не поставщики материалов) — мимо."""
    by_inn: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for n in lines:
        if n.line.supplier_inn:
            by_inn.setdefault(n.line.supplier_inn, n.line.supplier)
        by_name.setdefault(_norm(n.line.supplier), n.line.supplier)
    out: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        name = (by_inn.get(p.supplier_inn) if p.supplier_inn else None) or by_name.get(_norm(p.supplier))
        if name:
            out[name].append(p)
    return out


def profiles(lines: list[NormLine], payments: list[Payment], cfg: AuditConfig, end: date) -> dict[str, PaymentProfile]:
    deliveries: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for n in lines:
        deliveries[n.line.supplier].append((n.line.date, n.line.amount))
    out = {}
    for supplier, pays in match_suppliers(lines, payments).items():
        out[supplier] = _profile(supplier, deliveries.get(supplier, []), pays, cfg, end)
    return out


def _profile(
    supplier: str, deliveries: list[tuple[date, Decimal]], pays: list[Payment], cfg: AuditConfig, end: date
) -> PaymentProfile:
    # в один день сначала поставка, потом оплата: оплата в день поставки — постоплата с задержкой 0
    events = sorted(
        [(d, 0, a, None) for d, a in deliveries] + [(p.date, 1, p.amount, p) for p in pays], key=lambda e: e[:2]
    )
    unpaid: deque[list] = deque()  # [дата поставки, остаток]
    advances: deque[list] = deque()  # [дата оплаты, остаток, оплата]
    prepaid = postpaid = weighted = D0
    balance = D0
    above_since: date | None = None  # с какой даты превышение оплат над поставками не ниже порога
    floor = cfg.link_advance_min
    for d, kind, amount, pay in events:
        rest = amount
        if kind == 0:  # поставка гасит ранние оплаты
            while rest > 0 and advances:
                take = min(advances[0][1], rest)
                prepaid += take
                advances[0][1] -= take
                rest -= take
                if advances[0][1] <= 0:
                    advances.popleft()
            if rest > 0:
                unpaid.append([d, rest])
            balance -= amount
        else:  # оплата гасит ранние поставки
            while rest > 0 and unpaid:
                take = min(unpaid[0][1], rest)
                postpaid += take
                weighted += take * (d - unpaid[0][0]).days
                unpaid[0][1] -= take
                rest -= take
                if unpaid[0][1] <= 0:
                    unpaid.popleft()
            if rest > 0:
                advances.append([d, rest, pay])
            balance += amount
        if balance < floor:
            above_since = None
        elif above_since is None:
            above_since = d
    advance = sum((a[1] for a in advances), D0)
    since = above_since if advance >= floor else None
    return PaymentProfile(
        supplier=supplier,
        paid=sum((p.amount for p in pays), D0),
        delivered=sum((a for _, a in deliveries), D0),
        payments=len(pays),
        prepaid=prepaid,
        postpaid=postpaid,
        delay_days=(weighted / postpaid) if postpaid else None,
        advance=advance,
        advance_since=since,
        unpaid=sum((u[1] for u in unpaid), D0),
        since_payments=[
            {"date": x.date.isoformat(), "amount": str(_money(x.amount)), "doc": x.doc_no, "purpose": x.purpose}
            for x in sorted(pays, key=lambda x: x.date)
            if since and x.date >= since
        ],
        since_paid=sum((x.amount for x in pays if since and x.date >= since), D0),
        since_delivered=sum((a for d, a in deliveries if since and d >= since), D0),
    )


def _days(n: int) -> str:
    w = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
    return f"{n} {w}"


def _hanging(p: PaymentProfile, cfg: AuditConfig, end: date) -> int | None:
    """Сколько дней держится аванс без поставки — или None, если аванса нет или он свежий."""
    if p.advance < cfg.link_advance_min or p.advance_since is None:
        return None
    days = (end - p.advance_since).days
    return days if days >= cfg.link_advance_days else None


def advance_findings(
    profs: dict[str, PaymentProfile], lines: list[NormLine], cfg: AuditConfig, end: date
) -> list[AuditFinding]:
    """P-09: аванс без поставки — вопрос бухгалтерии и снабжению: где материал или возврат."""
    who: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: D0))
    for n in lines:
        if n.line.responsible:
            who[n.line.supplier][n.line.responsible] += n.line.amount
    out = []
    for supplier, p in profs.items():
        days = _hanging(p, cfg, end)
        if days is None:
            continue
        buyers = who.get(supplier) or {}
        main = max(buyers, key=lambda b: buyers[b]) if buyers else None
        out.append(
            AuditFinding(
                code="P-09",
                severity="high" if p.advance >= Decimal("1000000") else "medium",
                title="Аванс без поставки",
                message=(
                    f"{_q(supplier)}: оплачено на {_fmt(p.advance)} сом больше, чем поставлено; превышение держится "
                    f"с {p.advance_since:%d.%m.%Y} — {_days(days)} к концу периода. Где материал или возврат денег?"
                ),
                line_ids=[],
                supplier=supplier,
                reference_kind="advance",
                evidence={
                    "responsible": main,
                    "advance": str(_money(p.advance)),
                    "advance_since": p.advance_since.isoformat() if p.advance_since else None,
                    "days": days,
                    "paid": str(_money(p.paid)),
                    "delivered": str(_money(p.delivered)),
                    "since_paid": str(_money(p.since_paid)),
                    "since_delivered": str(_money(p.since_delivered)),
                    "since_payments": p.since_payments[:30],
                },
            )
        )
    return out


def payment_traces(profs: dict[str, PaymentProfile], cfg: AuditConfig, end: date) -> list[Trace]:
    """L-07: как платят поставщику — на фоне остальных поставщиков компании."""
    out = []
    for supplier, p in profs.items():
        days = _hanging(p, cfg, end)
        if days is not None:
            out.append(
                Trace(
                    code="L-07",
                    group="behavior",
                    text=(
                        f"Аванс {_fmt(p.advance)} сом без поставки держится {_days(days)}: "
                        "оплачено больше, чем поставлено"
                    ),
                    supplier=supplier,
                    money=_money(p.advance),
                    facts={"kind": "advance", "advance": str(_money(p.advance)), "days": days},
                )
            )
        others = [x for s, x in profs.items() if s != supplier]
        o_delivered = sum((x.delivered for x in others), D0)
        o_prepaid = sum((x.prepaid for x in others), D0)
        o_post = sum((x.postpaid for x in others), D0)
        o_weighted = sum(((x.delay_days or D0) * x.postpaid for x in others), D0)
        others_prepay = o_prepaid / o_delivered if o_delivered else None
        others_delay = o_weighted / o_post if o_post else None
        share = p.prepay_share
        if (
            share is not None
            and others_prepay is not None
            and p.delivered > 0
            and share >= cfg.link_prepay_share
            and others_prepay <= cfg.link_prepay_others_max
        ):
            out.append(
                Trace(
                    code="L-07",
                    group="behavior",
                    text=(
                        f"{_pct(share)} поставок {_q(supplier)} оплачены заранее, а остальным поставщикам "
                        f"платят после поставки (по предоплате — {_pct(others_prepay)})"
                    ),
                    supplier=supplier,
                    facts={
                        "kind": "prepay",
                        "share": str(share.quantize(Decimal("0.001"))),
                        "others": str(others_prepay.quantize(Decimal("0.001"))),
                    },
                )
            )
        elif (
            p.delay_days is not None
            and others_delay is not None
            and p.postpaid >= p.delivered / 2
            and p.delay_days <= cfg.link_fast_pay_days
            and others_delay >= cfg.link_fast_pay_others_min
        ):
            out.append(
                Trace(
                    code="L-07",
                    group="behavior",
                    text=(
                        f"{_q(supplier)} платят в среднем через {_days(int(p.delay_days.quantize(Decimal('1'))))} "
                        f"после поставки, остальным — через {_days(int(others_delay.quantize(Decimal('1'))))}"
                    ),
                    supplier=supplier,
                    facts={
                        "kind": "fast",
                        "delay": str(p.delay_days.quantize(Decimal("0.1"))),
                        "others": str(others_delay.quantize(Decimal("0.1"))),
                    },
                )
            )
    return out
