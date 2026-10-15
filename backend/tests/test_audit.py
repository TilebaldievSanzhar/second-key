"""Аудит закупок: импорт выгрузки 1С, нормализация номенклатуры, правила P-01…P-07."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pytest

from secondkey.engine.importer import import_registry, map_columns, missing_fields, parse_date
from secondkey.engine.models import PurchaseLine
from secondkey.engine.normalize import normalize, normalize_line
from secondkey.engine.pipeline import audit_lines
from secondkey.engine.rules import AuditConfig, p01_parallel_supplier, p03_above_market, p05_split, run_all


def _line(lid, d, supplier, item, qty, unit, price, doc=None, obj="ЖК", who="Снабженец"):
    qty, price = Decimal(str(qty)), Decimal(str(price))
    return PurchaseLine(
        line_id=lid,
        date=d,
        supplier=supplier,
        item_raw=item,
        qty=qty,
        unit_raw=unit,
        price=price,
        amount=qty * price,
        doc_no=doc,
        object_name=obj,
        responsible=who,
    )


def _sku(item, unit="т"):
    nl = normalize_line(_line("x", date(2026, 1, 1), "s", item, 1, unit, 100))
    return nl.sku, nl


# ------------------------------------------------------------------ импорт


def test_import_1c_layout_with_preamble(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Закупки материалов (поступление товаров и услуг)"])
    ws.append(["Период: 01.10.2025 - 30.09.2026"])
    ws.append([])
    ws.append(
        [
            "Дата",
            "Документ",
            "Контрагент",
            "ИНН контрагента",
            "Номенклатура",
            "Ед. изм.",
            "Количество",
            "Цена",
            "Сумма",
            "НДС",
            "Объект",
            "Ответственный",
        ]
    )
    ws.append(
        [
            date(2026, 3, 12),
            "Поступление 0001 от 12.03.2026",
            "ОсОО «А»",
            "012",
            "Арматура А500С ф12",
            "т",
            2,
            71500,
            143000,
            15321.43,
            "ЖК «Арча»",
            "Асанов Б.",
        ]
    )
    ws.append(
        [
            None,
            "Поступление 0002 от 13.03.2026",
            "ОсОО «Б»",
            "013",
            "Цемент М400 (мешок 50кг)",
            "меш",
            100,
            None,
            "41 500,00",
            None,
            "ЖК «Арча»",
            "Асанов Б.",
        ]
    )
    ws.append([date(2026, 3, 14), "0003", "ОсОО «В»", "014", "Песок мытый", "м3", 10, 1000, 12000, None, None, None])
    ws.append([None, None, None, None, "Итого", None, None, None, 196500, None, None, None])
    p = tmp_path / "reg.xlsx"
    wb.save(p)

    res = import_registry(p)
    assert res.header_row == 4
    assert {"date", "doc_no", "supplier", "supplier_inn", "item", "unit", "qty", "price", "amount", "vat"} <= set(
        res.mapping
    )
    assert len(res.lines) == 3
    a, b, c = res.lines
    assert a.price == Decimal("71500") and a.vat_included is True
    assert b.date == date(2026, 3, 13)  # дата из текста документа
    assert b.price == Decimal("415.00")  # цена из суммы
    assert c.price == Decimal("1200.00")  # 10 × 1000 ≠ 12000 → цена из суммы
    assert any("не равно сумме" in w for w in res.warnings)


def test_header_mapping_prefers_vat_over_amount():
    m = map_columns(["Дата", "Поставщик", "Наименование", "Кол-во", "Цена", "Сумма с НДС", "Сумма НДС"])
    assert m["amount"] == 5 and m["vat"] == 6


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12.03.2026", date(2026, 3, 12)),
        ("Поступление товаров 000123 от 01.02.26 10:15:00", date(2026, 2, 1)),
        ("2026-07-05", date(2026, 7, 5)),
        (46000, date(2025, 12, 9)),
        ("нет даты", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


# ------------------------------------------------------------------ нормализация


@pytest.mark.parametrize(
    "a,b",
    [
        ("Арматура А500С ф12", "Арм. d12 А500С L=11,7"),
        ("Арматура А500С ф12", "Арматура 12 мм А500С (мера 11,7м)"),
        ("Арматура А500С ф12", "Aрматура A500C ф12"),  # латиница внутри слов
        ("Цемент М400 (мешок 50кг)", "Портландцемент ПЦ400-Д20 50 кг"),
        ("Бетон М300 (В22,5)", "Бетон товарный B22.5 П4"),
        ("Бетон М300 (В22,5)", "БСГ М300 П4 F150 W6"),
        ("Щебень фр. 5-20", "ЩЕБЕНЬ ФР.5-20 ГОСТ 8267-93"),
        ("Кабель ВВГнг 3х2,5", "Кабель ВВГнг 3*2.5 (бухта 100м)"),
        ("Труба ППР PN20 ф20", "Труба полипропиленовая 20 PN20"),
        ("Песок мытый", "Песок речной мытый"),
    ],
)
def test_same_position(a, b):
    sa, na = _sku(a)
    sb, nb = _sku(b)
    assert na.status == nb.status == "ok", (na.note, nb.note)
    assert sa.split("|u=")[0] == sb.split("|u=")[0]


@pytest.mark.parametrize(
    "a,b",
    [
        ("Арматура А500С ф12", "Арматура А500С ф14"),
        ("Арматура А500С ф12", "Арматура А400 ф12"),
        ("Цемент М400", "Цемент М500"),
        ("Кабель ВВГнг 3х2,5", "Кабель ВВГнг 3х1,5"),
        ("Щебень фр. 5-20", "Щебень фр. 20-40"),
        ("Бетоноконтакт 20 кг", "Бетон М300"),
        ("Пескобетон М300 40 кг", "Песок мытый"),
    ],
)
def test_different_positions(a, b):
    assert _sku(a)[0] != _sku(b)[0]


def test_ambiguous_goes_to_human():
    nl = _sku("Арматура А500С")[1]  # нет диаметра
    assert nl.status == "needs_input"
    assert _sku("Плинтус МДФ")[1].status == "needs_input"


def test_unit_conversion_to_base():
    bag = normalize_line(_line("a", date(2026, 1, 1), "s", "Цемент М400 (мешок 50кг)", 100, "меш", 415))
    assert bag.unit == "t" and bag.qty == Decimal("5") and bag.price == Decimal("8300.00")
    kg = normalize_line(_line("b", date(2026, 1, 1), "s", "Арматура А500С ф12", 2000, "кг", "71.5"))
    assert kg.unit == "t" and kg.qty == Decimal("2") and kg.price == Decimal("71500.00")
    m = normalize_line(_line("c", date(2026, 1, 1), "s", "Арматура А500С ф12", 1000, "м", "63.492"))
    assert m.unit == "t" and m.qty == Decimal("0.888")  # 0,888 кг/м по ГОСТ 5781
    sheet = normalize_line(_line("d", date(2026, 1, 1), "s", "ГКЛ 12.5мм 1200*2500", 10, "шт", 520))
    assert sheet.unit == "sheet" and "|u=" not in sheet.sku


# ------------------------------------------------------------------ правила

D = date(2026, 3, 2)


def test_p01_flags_expensive_parallel_supplier():
    lines = [
        _line("a1", D, "Дешёвый", "Арматура А500С ф12", 10, "т", 70000),
        _line("a2", D + timedelta(days=10), "Дешёвый", "Арматура А500С ф12", 10, "т", 70500),
        _line("b1", D + timedelta(days=5), "Свой", "Арматура А500С ф12", 10, "т", 80000),
        _line("b2", D + timedelta(days=12), "Свой", "Арматура А500С ф12", 5, "т", 81000),
    ]
    f = p01_parallel_supplier(normalize(lines), AuditConfig())
    assert len(f) == 1 and f[0].supplier == "Свой" and f[0].code == "P-01"
    assert set(f[0].line_ids) == {"b1", "b2"}
    assert f[0].overpayment > Decimal("140000")


def test_p01_ignores_small_differences_and_far_dates():
    lines = [
        _line("a", D, "А", "Цемент М400", 10, "т", 8300),
        _line("b", D + timedelta(days=3), "Б", "Цемент М400", 10, "т", 8500),  # +2,4% < допуска
        _line("c", D + timedelta(days=90), "В", "Цемент М400", 10, "т", 9500),  # вне окна
    ]
    assert p01_parallel_supplier(normalize(lines), AuditConfig()) == []


def test_p03_above_market_conservative():
    lines = [_line("a", D, "Карьер", "Песок мытый", 100, "м3", 1400)]

    def market(sku, d, w):
        return [Decimal("1100"), Decimal("1150"), Decimal("1200")]

    f = p03_above_market(normalize(lines), AuditConfig(), market)
    assert len(f) == 1
    # переплата только сверх допуска 10%: (1400 − 1150 × 1,1) × 100
    assert f[0].overpayment == Decimal("13500")


def test_p05_split_near_threshold_same_object():
    cfg = AuditConfig(approval_threshold=Decimal("500000"))
    lines = [_line(f"s{i}", D + timedelta(days=i), "Цемент Опт", "Цемент М400", 55, "т", 8500) for i in range(4)]
    lines.append(_line("x", D + timedelta(days=2), "Цемент Опт", "Цемент М400", 10, "т", 8500))  # мелкая — не в шаблоне
    f = p05_split(normalize(lines), cfg)
    assert len(f) == 1 and set(f[0].line_ids) == {"s0", "s1", "s2", "s3"}


def test_duplicates_and_unit_errors_in_pipeline():
    base = [
        _line(f"r{i}", D + timedelta(days=i), "А", "Арматура А500С ф10", 5, "т", 73000, doc=f"{i:04d}")
        for i in range(5)
    ]
    dup = _line("dup", D + timedelta(days=1), "А", "Арматура А500С ф10", 5, "т", 73000, doc="0001")
    wrong = _line("unit", D + timedelta(days=2), "А", "Арматура А500С ф10", 5000, "т", "73")  # кг под видом т
    res = audit_lines([*base, dup, wrong], "Тест")
    codes = {f.code: f for f in res.findings}
    assert "P-06" in codes and set(codes["P-06"].line_ids) == {"r1", "dup"}
    # повтор оценивается один раз: сумма одной из двух одинаковых строк
    assert sum(res.line_overpayment.get(l, Decimal("0")) for l in ("r1", "dup")) == Decimal("365000")
    assert "P-07" in codes and "unit" in codes["P-07"].line_ids
    # строка с ошибкой единицы не порождает «переплату»
    assert "unit" not in res.line_overpayment


def test_no_double_counting_across_rules():
    lines = [
        _line("a", D, "Дешёвый", "Песок мытый", 100, "м3", 1100),
        _line("b", D + timedelta(days=2), "Дорогой", "Песок мытый", 100, "м3", 1400),
    ]

    def market(sku, d, w):
        return [Decimal("1100")] * 3

    res = audit_lines(lines, "Тест", market=type("M", (), {"lookup": staticmethod(market)})())
    per_rule = [f.line_overpayment.get("b", Decimal("0")) for f in res.findings]
    assert len([v for v in per_rule if v > 0]) == 2  # P-01 и P-03
    assert res.line_overpayment["b"] == max(per_rule)  # в итог — максимум, не сумма
    assert run_all(normalize(lines), AuditConfig()) is not None


def test_missing_fields_needs_price_or_amount():
    base = {"date": 0, "supplier": 1, "item": 2, "qty": 3}
    assert missing_fields(base) == ["price"]
    assert missing_fields({**base, "amount": 4}) == []
    assert missing_fields({"date": 0}) == ["supplier", "item", "qty", "price"]
