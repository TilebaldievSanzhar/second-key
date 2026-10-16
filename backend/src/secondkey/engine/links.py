"""Связки снабженца и поставщика: следы в цене, в поведении и в личности поставщика. Только детерминированный код.

Откат не виден в учёте, но оставляет следы (docs/product/SUPPLIER_LINKS.md):
  цена       L-01 премия пары: ту же позицию в те же недели другие снабженцы покупают дешевле
             P-01/P-02/P-03 — переплата по строкам пары, найденная правилами цены
  поведение  L-04 эксклюзивность: почти весь оборот поставщика — через одного снабженца
             L-05 поставщик появился вместе с новым снабженцем
             L-06 вытеснение: новый поставщик забрал позицию и он дороже прежних
  личность   L-09 вы — почти единственный клиент: входящие номера его документов идут подряд
             L-10 юрлицо моложе года или ИП-«номинал» — по номеру ИНН
             L-11 общие телефон, счёт, e-mail с другим поставщиком
             L-12 ИНН поставщика совпадает с ИНН сотрудника

Связка становится вопросом (находка LINK), когда у пары сошлись следы из двух и более групп или
поставщик совпал с сотрудником. Один след почти всегда объясним; совпадение независимых — повод спросить.

Деньги связка не добавляет: переплату по строкам считают правила P-xx. У находки LINK вклад строк — 0,
а overpayment — справочно: сколько найденной переплаты приходится на строки пары.
Текст находки (message) видит снабженец — в нём только факты о покупках; следы личности и поведения —
в evidence, их видит собственник.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from itertools import pairwise

from secondkey.engine.inn import parse_inn
from secondkey.engine.models import AuditFinding, NormLine, StaffRef, SupplierCard, SupplierProfile, Trace
from secondkey.engine.rules import AuditConfig, _fmt, _groups, _in_window, _median, _money, _pct, _q


def _dec(v: Decimal, places: str = "0.001") -> Decimal:
    return v.quantize(Decimal(places))


D0 = Decimal("0")
PRICE_RULES = ("P-01", "P-02", "P-03")
GROUP_TITLE = {"price": "цена", "behavior": "поведение", "identity": "поставщик"}
MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _d(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def _years(n: float) -> str:
    y = int(n)
    word = "год" if y % 10 == 1 and y % 100 != 11 else "года" if 2 <= y % 10 <= 4 and not 12 <= y % 100 <= 14 else "лет"
    return f"{y} {word}"


def _purchases(n: int) -> str:
    word = (
        "покупка"
        if n % 10 == 1 and n % 100 != 11
        else "покупки"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14
        else "покупок"
    )
    return f"{n} {word}"


def _months(days: int) -> str:
    m = max(1, round(days / 30.4))
    word = (
        "месяц"
        if m % 10 == 1 and m % 100 != 11
        else "месяца"
        if 2 <= m % 10 <= 4 and not 12 <= m % 100 <= 14
        else "месяцев"
    )
    return f"{m} {word}"


class _Data:
    """Срезы строк, общие для всех сигналов."""

    def __init__(self, lines: list[NormLine]):
        self.lines = lines
        self.by_supplier: dict[str, list[NormLine]] = defaultdict(list)
        for n in lines:
            self.by_supplier[n.line.supplier].append(n)
        for v in self.by_supplier.values():
            v.sort(key=lambda n: (n.line.date, n.line.line_id))
        self.total = sum((n.line.amount for n in lines), D0)
        self.start = min((n.line.date for n in lines), default=None)
        self.end = max((n.line.date for n in lines), default=None)
        self.spend = {s: sum((n.line.amount for n in v), D0) for s, v in self.by_supplier.items()}
        self.buyer_first: dict[str, date] = {}
        for n in lines:
            b = n.line.responsible
            if b and (b not in self.buyer_first or n.line.date < self.buyer_first[b]):
                self.buyer_first[b] = n.line.date
        self.inn = {
            s: Counter(n.line.supplier_inn for n in v if n.line.supplier_inn).most_common(1)
            for s, v in self.by_supplier.items()
        }
        self.inn = {s: (c[0][0] if c else None) for s, c in self.inn.items()}

    def buyers(self, supplier: str) -> list[tuple[str, Decimal, int]]:
        acc: dict[str, list] = defaultdict(lambda: [D0, 0])
        for n in self.by_supplier[supplier]:
            if n.line.responsible:
                acc[n.line.responsible][0] += n.line.amount
                acc[n.line.responsible][1] += 1
        return sorted(((b, v[0], v[1]) for b, v in acc.items()), key=lambda x: -x[1])

    def main_buyer(self, supplier: str) -> tuple[str | None, Decimal]:
        bs = self.buyers(supplier)
        spend = self.spend[supplier]
        if not bs or not spend:
            return None, D0
        return bs[0][0], bs[0][1] / spend


# --------------------------------------------------------------------------- цена


def l01_pair_premium(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Ту же позицию в том же окне другие снабженцы покупают дешевле — премия держится через одного человека."""
    acc: dict[tuple[str, str], dict] = {}
    for _sku, grp in _groups(data.lines).items():
        if len({n.line.responsible for n in grp if n.line.responsible}) < 2:
            continue  # позицию покупает один человек — сравнивать не с кем (категории закреплены)
        for n in grp:
            a = n.line.responsible
            if not a:
                continue
            others: dict[str, list[tuple[int, Decimal]]] = defaultdict(list)
            for o in grp:
                b = o.line.responsible
                if b and b != a and _in_window(n, o, cfg.parallel_window_days):
                    others[b].append((abs((o.line.date - n.line.date).days), o.price))
            if not others:
                continue
            refs = {b: _median([p for _, p in sorted(v)[: cfg.parallel_nearest]]) for b, v in others.items()}
            ref_buyer, ref = min(refs.items(), key=lambda x: x[1])
            if n.price > ref * (1 + cfg.parallel_tolerance):
                x = acc.setdefault((a, n.line.supplier), {"lines": {}, "prem": [], "others": set(), "skus": Counter()})
                x["lines"][n.line_id] = (n.price - ref) * n.qty
                x["prem"].append(n.price / ref - 1)
                x["others"].add(ref_buyer)
                x["skus"][n.sku_label] += 1
    out = []
    for (a, supplier), x in acc.items():
        if len(x["lines"]) < cfg.link_min_lines:
            continue
        prem = _median(x["prem"])
        money = _money(sum(x["lines"].values(), D0))
        sku = x["skus"].most_common(1)[0][0]
        others = ", ".join(sorted(x["others"]))
        out.append(
            Trace(
                code="L-01",
                group="price",
                text=(
                    f"{sku}: у {_q(supplier)} (закупки оформлял {a}) в среднем на {_pct(prem)} дороже, "
                    f"чем в те же недели покупает {others}; покупок {len(x['lines'])}, разница {_fmt(money)} сом"
                ),
                supplier=supplier,
                responsible=a,
                money=money,
                line_ids=list(x["lines"]),
                facts={"premium_median": str(_dec(prem)), "compared_with": sorted(x["others"]), "sku": sku},
            )
        )
    return out


def price_traces(data: _Data, findings: list[AuditFinding], cfg: AuditConfig) -> list[Trace]:
    """Переплата, найденная правилами цены, по строкам пары «снабженец — поставщик»."""
    by_line = {n.line_id: n for n in data.lines}
    acc: dict[tuple[str | None, str], dict] = {}
    for f in findings:
        if f.code not in PRICE_RULES:
            continue
        for lid, v in f.line_overpayment.items():
            n = by_line.get(lid)
            if n is None or v <= 0:
                continue
            x = acc.setdefault((n.line.responsible, n.line.supplier), {"lines": {}, "codes": Counter(), "msg": {}})
            if v > x["lines"].get(lid, D0):
                x["lines"][lid] = v
            x["codes"][f.code] += v
            x["msg"].setdefault(f.code, f.message)
    out = []
    for (a, supplier), x in acc.items():
        money = _money(sum(x["lines"].values(), D0))
        if money < cfg.link_price_money_min:
            continue
        code = x["codes"].most_common(1)[0][0]
        text = x["msg"][code]
        if len(x["msg"]) > 1 or sum(1 for f in findings if f.supplier == supplier and f.code in PRICE_RULES) > 1:
            text = f"{text}. Всего по ценовым вопросам к этим покупкам — {_fmt(money)} сом"
        out.append(
            Trace(
                code=code,
                group="price",
                text=text,
                supplier=supplier,
                responsible=a,
                money=money,
                line_ids=list(x["lines"]),
                facts={"rules": sorted(x["codes"])},
            )
        )
    return out


# --------------------------------------------------------------------------- поведение


def l04_exclusive(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Почти весь оборот поставщика по общим позициям — через одного снабженца, хотя их покупают несколько."""
    buyers_of_sku: dict[str, set[str]] = defaultdict(set)
    for n in data.lines:
        if n.sku and n.line.responsible:
            buyers_of_sku[n.sku].add(n.line.responsible)
    out = []
    for supplier, lines in data.by_supplier.items():
        shared = [n for n in lines if n.sku and len(buyers_of_sku[n.sku]) >= 2 and n.line.responsible]
        if len(shared) < cfg.link_min_lines:
            continue
        spend = Counter()
        for n in shared:
            spend[n.line.responsible] += n.line.amount
        total = sum(spend.values(), D0)
        a, v = spend.most_common(1)[0]
        share = v / total if total else D0
        if share < cfg.link_exclusive_share:
            continue
        sku_lines = Counter(n.sku_label for n in shared if n.line.responsible == a)
        sku = sku_lines.most_common(1)[0][0]
        others = sorted({b for n in shared for b in buyers_of_sku[n.sku]} - {a})
        out.append(
            Trace(
                code="L-04",
                group="behavior",
                text=(
                    f"{_pct(share)} закупок у {_q(supplier)} оформил один снабженец — {a}, хотя те же позиции ({sku}"
                    f"{' и др.' if len(sku_lines) > 1 else ''}) покупает и {', '.join(others)} — у других поставщиков"
                ),
                supplier=supplier,
                responsible=a,
                line_ids=[n.line_id for n in shared if n.line.responsible == a],
                facts={"share": str(_dec(share)), "other_buyers": others},
            )
        )
    return out


def l05_came_with_buyer(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Поставщик появился вскоре после первой закупки нового снабженца и работает почти только через него."""
    if data.start is None:
        return []
    out = []
    for supplier, lines in data.by_supplier.items():
        if len(lines) < cfg.link_min_lines:
            continue
        a, share = data.main_buyer(supplier)
        if a is None or share < cfg.link_exclusive_share:
            continue
        fa = data.buyer_first[a]
        if (fa - data.start).days < cfg.link_new_buyer_days:
            continue  # снабженец работает с начала периода — «пришёл вместе» не определить
        fx = lines[0].line.date
        lag = (fx - fa).days
        if -7 <= lag <= cfg.link_came_with_days:
            out.append(
                Trace(
                    code="L-05",
                    group="behavior",
                    text=(
                        f"Первая закупка {a} — {_d(fa)}; {_q(supplier)} появился "
                        f"{'в те же дни' if lag <= 3 else f'через {lag} дн.'} "
                        f"и {_pct(share)} его оборота оформил {a}"
                    ),
                    supplier=supplier,
                    responsible=a,
                    line_ids=[lines[0].line_id],
                    facts={"buyer_first": fa.isoformat(), "supplier_first": fx.isoformat(), "lag_days": lag},
                )
            )
    return out


def l06_displacement(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Новый поставщик забрал заметную долю позиции, он дороже прежних, переход сделал один снабженец."""
    if data.start is None:
        return []
    out = []
    for _sku, grp in _groups(data.lines).items():
        suppliers = {n.line.supplier for n in grp}
        if len(suppliers) < 2:
            continue
        for x in suppliers:
            xs = [n for n in grp if n.line.supplier == x]
            fx = xs[0].line.date
            before = [n for n in grp if n.line.date < fx]
            # новый поставщик (впервые в периоде) пришёл, когда у позиции уже была история закупок у других;
            # давний поставщик, который начал возить ещё одну позицию, — не вытеснение
            if (
                len(xs) < cfg.link_min_lines
                or len(before) < cfg.link_min_lines
                or (fx - grp[0].line.date).days < 2 * cfg.link_new_buyer_days
                or (data.by_supplier[x][0].line.date - data.start).days < 2 * cfg.link_new_buyer_days
            ):
                continue
            after = [n for n in grp if n.line.date >= fx]
            vol_after = sum((n.line.amount for n in after), D0)
            share_x = sum((n.line.amount for n in xs), D0) / vol_after if vol_after else D0
            others_after = [n for n in after if n.line.supplier != x]
            if share_x < cfg.link_displace_share or not others_after or not before:
                continue
            premium = _median([n.price for n in xs]) / _median([n.price for n in others_after]) - 1
            if premium < cfg.parallel_tolerance:
                continue
            who = Counter(n.line.responsible for n in xs if n.line.responsible)
            if not who:
                continue
            a, cnt = who.most_common(1)[0]
            if Decimal(cnt) / len(xs) < cfg.link_exclusive_share:
                continue

            def share_of(rows: list[NormLine], s: str) -> Decimal:
                tot = sum((n.line.amount for n in rows), D0)
                return sum((n.line.amount for n in rows if n.line.supplier == s), D0) / tot if tot else D0

            lost = max(suppliers - {x}, key=lambda s: share_of(before, s) - share_of(after, s))
            sb, sa = share_of(before, lost), share_of(after, lost)
            label = xs[0].sku_label
            out.append(
                Trace(
                    code="L-06",
                    group="behavior",
                    text=(
                        f"{label}: с {_d(fx)} {_q(x)} забрал {_pct(share_x)} закупок и он на {_pct(premium)} дороже "
                        f"остальных; доля {_q(lost)} — {_pct(sb)} → {_pct(sa)}; закупки у {_q(x)} оформлял {a}"
                    ),
                    supplier=x,
                    responsible=a,
                    line_ids=[n.line_id for n in xs],
                    facts={
                        "sku": label,
                        "share_after": str(_dec(share_x)),
                        "premium": str(_dec(premium)),
                        "displaced": lost,
                        "displaced_share": [str(_dec(sb)), str(_dec(sa))],
                    },
                )
            )
    return out


# --------------------------------------------------------------------------- личность поставщика

_NUM = re.compile(r"(\d+)\D*$")


def l09_sequential_numbers(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Входящие номера документов поставщика идут почти подряд — у него почти нет других клиентов.

    Мелкому узкому поставщику это бывает нормой, поэтому сам по себе след — не вопрос.
    """
    out = []
    for supplier, lines in data.by_supplier.items():
        docs: dict[tuple[str, int], date] = {}
        for n in lines:
            raw = (n.line.doc_in_no or "").strip()
            m = _NUM.search(raw)
            if not m:
                continue
            prefix = raw[: m.start()].strip().lower()
            num = int(m.group(1))
            d = n.line.doc_in_date or n.line.date
            if (prefix, num) not in docs or d < docs[(prefix, num)]:
                docs[(prefix, num)] = d
        if len(docs) < cfg.link_seq_min_docs:
            continue
        prefix = Counter(p for p, _ in docs).most_common(1)[0][0]
        seq = sorted(((d, num) for (p, num), d in docs.items() if p == prefix))
        if len(seq) < cfg.link_seq_min_docs or (seq[-1][0] - seq[0][0]).days < 60:
            continue
        steps = [b[1] - a[1] for a, b in pairwise(seq)]
        if sum(1 for s in steps if s > 0) / len(steps) < 0.8:
            continue
        lo, hi = min(n for _, n in seq), max(n for _, n in seq)
        median_step = statistics.median([s for s in steps if s > 0])
        span = hi - lo + 1
        share = Decimal(len(seq)) / span
        if median_step > cfg.link_seq_max_step or share < cfg.link_seq_min_share:
            continue
        days = (seq[-1][0] - seq[0][0]).days
        out.append(
            Trace(
                code="L-09",
                group="identity",
                text=(
                    f"Входящие номера документов {_q(supplier)} за {_months(days)} идут почти подряд: "
                    f"с {lo} по {hi} — {len(seq)} из {span} номеров выписаны вам. "
                    "Похоже, других покупателей у поставщика почти нет"
                ),
                supplier=supplier,
                line_ids=[n.line_id for n in lines if n.line.doc_in_no],
                facts={"first": lo, "last": hi, "ours": len(seq), "span": span, "median_step": median_step},
            )
        )
    return out


def l10_inn_age(data: _Data, cfg: AuditConfig) -> list[Trace]:
    """Юрлицо зарегистрировано меньше чем за год до первой поставки; ИП моложе 23 или старше 70 лет."""
    out = []
    for supplier, lines in data.by_supplier.items():
        info = parse_inn(data.inn.get(supplier))
        if info is None or info.date is None or not data.total:
            continue
        share = data.spend[supplier] / data.total
        if share < cfg.link_min_spend_share:
            continue
        first = lines[0].line.date
        age = (first - info.date).days
        if info.kind == "org" and 0 <= age <= cfg.link_young_org_days:
            text = (
                f"{_q(supplier)} зарегистрирован {_d(info.date)} (по номеру ИНН) — "
                f"за {_months(age)} до первой поставки; "
                f"с тех пор {_pct(share)} ваших закупок, {_fmt(data.spend[supplier])} сом"
            )
        elif info.kind == "person":
            years = age / 365.25
            if cfg.link_person_min_age <= years <= cfg.link_person_max_age:
                continue
            text = (
                f"{_q(supplier)}: по номеру ИНН предпринимателю {_years(years)} на дату первой поставки, "
                f"а оборот с вами — {_fmt(data.spend[supplier])} сом ({_pct(share)} закупок). "
                "Так бывает, когда ИП оформлен на подставное лицо"
            )
        else:
            continue
        out.append(
            Trace(
                code="L-10",
                group="identity",
                text=text,
                supplier=supplier,
                line_ids=[lines[0].line_id],
                facts={"inn_kind": info.kind, "inn_date": info.date.isoformat(), "days_before_first": age},
            )
        )
    return out


def _norm_phone(v: str | None) -> str | None:
    d = re.sub(r"\D", "", v or "")
    return d[-9:] if len(d) >= 9 else None


def _norm_account(v: str | None) -> str | None:
    d = re.sub(r"\D", "", v or "")
    return d if len(d) >= 10 else None


def _norm_email(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    return v if "@" in v else None


def _norm_address(v: str | None) -> str | None:
    v = re.sub(r"[^\w]+", " ", (v or "").lower()).strip()
    return v if len(v) >= 10 else None


def l11_shared_requisites(data: _Data, cards: dict[str, SupplierCard]) -> list[Trace]:
    """У «разных» поставщиков совпадают телефон, расчётный счёт или e-mail; адрес — только вместе с другим."""
    fields = (
        ("phone", _norm_phone, "телефон"),
        ("bank_account", _norm_account, "расчётный счёт"),
        ("email", _norm_email, "e-mail"),
        ("address", _norm_address, "адрес"),
    )
    index: dict[tuple[str, str], set[str]] = defaultdict(set)
    for name, card in cards.items():
        if name not in data.by_supplier:
            continue
        for attr, norm, _ in fields:
            v = norm(getattr(card, attr))
            if v:
                index[(attr, v)].add(name)
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (attr, _), names in index.items():
        if len(names) < 2:
            continue
        ns = sorted(names)
        for i, a in enumerate(ns):
            for b in ns[i + 1 :]:
                if data.inn.get(a) and data.inn.get(a) == data.inn.get(b):
                    continue  # один и тот же контрагент заведён дважды — это не «конкуренты»
                pairs[(a, b)].append(attr)
    label = {attr: title for attr, _, title in fields}
    out = []
    for (a, b), attrs in pairs.items():
        if attrs == ["address"]:
            continue  # один бизнес-центр — ещё не связь
        what = ", ".join(label[x] for x in attrs)
        for me, other in ((a, b), (b, a)):
            out.append(
                Trace(
                    code="L-11",
                    group="identity",
                    text=(
                        f"У {_q(me)} и {_q(other)} совпадают реквизиты: {what} — "
                        "«конкуренты» могут быть одним владельцем"
                    ),
                    supplier=me,
                    line_ids=[data.by_supplier[me][0].line_id],
                    facts={"other": other, "fields": attrs},
                )
            )
    return out


def l12_staff(data: _Data, staff: list[StaffRef], hash_inn: Callable[[str], str] | None) -> list[Trace]:
    """ИНН поставщика совпадает с ИНН сотрудника (сравниваются хэши)."""
    if not staff or hash_inn is None:
        return []
    by_hash = {s.inn_hash: s for s in staff if s.inn_hash}
    out = []
    for supplier, lines in data.by_supplier.items():
        inn = data.inn.get(supplier)
        s = by_hash.get(hash_inn(inn)) if inn else None
        if s is None:
            continue
        role = f" ({s.role})" if s.role else ""
        out.append(
            Trace(
                code="L-12",
                group="identity",
                text=(
                    f"ИНН {_q(supplier)} совпадает с ИНН сотрудника {s.name}{role}: "
                    f"закупки у этого поставщика — {_fmt(data.spend[supplier])} сом"
                ),
                supplier=supplier,
                line_ids=[n.line_id for n in lines],
                facts={"staff": s.name, "role": s.role},
            )
        )
    return out


# --------------------------------------------------------------------------- триангуляция


def run_links(
    lines: list[NormLine],
    findings: list[AuditFinding],
    cfg: AuditConfig | None = None,
    cards: dict[str, SupplierCard] | None = None,
    staff: list[StaffRef] | None = None,
    hash_inn: Callable[[str], str] | None = None,
) -> tuple[list[AuditFinding], list[SupplierProfile]]:
    """Следы по всем поставщикам → досье поставщиков и находки LINK для пар, где следы сошлись."""
    cfg = cfg or AuditConfig()
    cards = cards or {}
    data = _Data(lines)
    if not lines:
        return [], []
    traces = (
        l01_pair_premium(data, cfg)
        + price_traces(data, findings, cfg)
        + l04_exclusive(data, cfg)
        + l05_came_with_buyer(data, cfg)
        + l06_displacement(data, cfg)
        + l09_sequential_numbers(data, cfg)
        + l10_inn_age(data, cfg)
        + l11_shared_requisites(data, cards)
        + l12_staff(data, staff or [], hash_inn)
    )
    by_supplier: dict[str, list[Trace]] = defaultdict(list)
    for t in traces:
        by_supplier[t.supplier].append(t)

    out: list[AuditFinding] = []
    profiles: list[SupplierProfile] = []
    for supplier in data.by_supplier:
        a, share = data.main_buyer(supplier)
        mine = by_supplier.get(supplier, [])
        profiles.append(_profile(data, supplier, cards.get(supplier), mine))
        pair = a if share >= Decimal("0.5") else None
        # следы пары — этого снабженца; следы поставщика (личность) — к основной паре
        relevant = [t for t in mine if t.responsible in (None, pair)]
        groups = {t.group for t in relevant}
        if not (len(groups) >= 2 or any(t.code == "L-12" for t in relevant)):
            continue
        out.append(_finding(data, supplier, pair, share, relevant))
    profiles.sort(key=lambda p: -p.spend)
    return out, profiles


def _profile(data: _Data, supplier: str, card: SupplierCard | None, traces: list[Trace]) -> SupplierProfile:
    rows = data.by_supplier[supplier]
    info = parse_inn(data.inn.get(supplier))
    spend = data.spend[supplier]
    return SupplierProfile(
        name=supplier,
        inn=data.inn.get(supplier),
        inn_kind=info.kind if info else None,
        inn_date=info.date if info else None,
        spend=spend,
        lines=len(rows),
        share=_dec(spend / data.total, "0.0001") if data.total else D0,
        first_date=rows[0].line.date,
        last_date=rows[-1].line.date,
        buyers=[{"name": b, "spend": str(v), "lines": n} for b, v, n in data.buyers(supplier)],
        skus=len({n.sku for n in rows if n.sku}),
        card=card,
        traces=traces,
    )


def _finding(data: _Data, supplier: str, a: str | None, share: Decimal, traces: list[Trace]) -> AuditFinding:
    rows = [n for n in data.by_supplier[supplier] if a is None or n.line.responsible == a]
    spend = sum((n.line.amount for n in rows), D0)
    price = [t for t in traces if t.group == "price"]
    # справочно: самая крупная оценка по ценовым следам; в итог прогона не идёт (вклад строк LINK — 0)
    money = max([t.money for t in price], default=D0)
    groups = sorted({t.group for t in traces}, key=["price", "behavior", "identity"].index)
    strong = any(t.code == "L-12" for t in traces) or len(groups) == 3 or money >= Decimal("1000000")
    sku = Counter(n.sku_label for n in rows if n.sku_label).most_common(1)
    who = f"оформлял в основном {a}" if a else "оформляли разные сотрудники"
    lead = f"Закупки у {_q(supplier)} {who}: {_purchases(len(rows))} на {_fmt(spend)} сом"
    # снабженцу — только факты о покупках: ценовые следы; остальное — в evidence для собственника
    price_text = next((t.text for t in price if t.code == "L-01"), None) or next((t.text for t in price), None)
    message = f"{lead}. {price_text}" if price_text else f"{lead}. Как выбирали этого поставщика?"
    return AuditFinding(
        code="LINK",
        severity="high" if strong else "medium",
        title="Связка снабженца и поставщика",
        message=message,
        line_ids=[n.line_id for n in rows],
        overpayment=money,
        sku=None,
        sku_label=sku[0][0] if sku else None,
        supplier=supplier,
        reference_kind="link",
        evidence={
            "responsible": a,
            "pair_share": str(_dec(share)),
            "spend": str(_money(spend)),
            "groups": groups,
            "traces": [
                {
                    "code": t.code,
                    "group": t.group,
                    "text": t.text,
                    "money": str(t.money),
                    "facts": t.facts,
                }
                for t in sorted(traces, key=lambda t: (["price", "behavior", "identity"].index(t.group), t.code))
            ],
        },
    )
