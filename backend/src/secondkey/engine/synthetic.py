"""Синтетический реестр закупок застройщика (выгрузка «как из 1С») с ground truth.

Мир генератора:
- вымышленная компания с двумя ЖК в Бишкеке, 12 месяцев (10.2025–09.2026);
- 38 позиций материалов с ценами Бишкека (сом с НДС), трендом и сезонностью;
- 17 поставщиков с обычной наценкой ±2% и шумом строки ±1,5%;
- формулировки номенклатуры как в живом учёте: 2–4 варианта на позицию,
  у каждого снабженца свой, плюс капс и латиница в марках;
- единицы вперемешку: мешки и тонны, кубы и «куб», вёдра и кг.

Подсаженные нарушения (тип = правило, которое должно их ловить):
  A1 P-01  снабженец уводит часть арматуры ⌀12/⌀14 к «своему» поставщику (+15%)
  A2 P-02  поставщик отделки поднимает цену шпаклёвки и ГКЛ на 2,5% в месяц
  A3 P-03  песок и щебень у основного поставщика на 22% дороже рынка
  A4 P-05  цемент дробится на 4 покупки чуть ниже порога согласования
  A5 P-06  повтор документа
  A6 P-07  ошибка единицы (цена за кг в строке с единицей «т»)
  A8 P-01  срочные покупки в рознице (+7%) при живом оптовом поставщике
Истинная переплата строки = цена строки − честная цена основного поставщика на ту же дату.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

START = date(2025, 10, 1)
MONTHS = 12
COMPANY = "ОсОО «Ала-Тоо Курулуш» (вымышленная компания)"
OBJECTS = {
    "ЖК «Арча», Бишкек": {"structure": range(0, 8), "finish": range(5, 12)},
    "ЖК «Ордо», Бишкек": {"structure": range(3, 12), "finish": range(9, 12)},
}
VAT_RATE_KG = Decimal("0.12")
APPROVAL_THRESHOLD = Decimal("500000")

# поставщик → (обычная наценка к рынку, ИНН)
SUPPLIERS = {
    "ОсОО «Темир Снаб»": ("0.99", "01204201810015"),
    "ОсОО «Металл Базар КГ»": ("1.01", "01503201910042"),
    "ИП Сыдыков А.Т.": ("1.00", "21102198500311"),
    "ОсОО «Кант Цемент Снаб»": ("0.99", "02201201610027"),
    "ОсОО «Цемент Опт»": ("1.01", "01107202110058"),
    "ИП Жумабаев Н.": ("1.00", "20909197900124"),
    "ОсОО «Ала-Тоо Карьер»": ("1.00", "01806201510036"),
    "ОсОО «Бетон Профи»": ("1.00", "00304201710064"),
    "ОсОО «БишкекБетон»": ("1.015", "01212201410071"),
    "ОсОО «Кирпичный двор»": ("1.00", "02505201310019"),
    "ОсОО «Электро Трейд»": ("0.99", "01911201810083"),
    "ОсОО «ТрубаСнаб»": ("1.00", "02708202010047"),
    "ОсОО «Отделка Центр»": ("1.00", "00602201910095"),
    "ОсОО «Лесторг»": ("1.00", "01001201210061"),
    "ИП Бекова С.": ("1.02", "11407198800452"),
    "ОсОО «Изоляция КГ»": ("1.00", "02103202210038"),
    "ОсОО «Крепёж Плюс»": ("0.99", "01408201610077"),
    "ОсОО «Строймаркет Восток»": ("1.07", "00509201510026"),  # розница
}
RETAIL = "ОсОО «Строймаркет Восток»"
WHO = {
    "metal": "Асанов Б.",
    "cement": "Асанов Б.",
    "concrete": "Асанов Б.",
    "aggregate": "Токтосунов Э.",
    "masonry": "Токтосунов Э.",
    "timber": "Токтосунов Э.",
    "insul": "Токтосунов Э.",
    "finish": "Исаева Г.",
    "electric": "Исаева Г.",
    "pipe": "Исаева Г.",
    "fastener": "Исаева Г.",
}
WHO_INDEX = {"Асанов Б.": 0, "Токтосунов Э.": 1, "Исаева Г.": 2}

# группа → (тренд в месяц, сезонная надбавка май–сентябрь)
TRENDS = {
    "metal": ("0.008", "0"),
    "cement": ("0.005", "0.06"),
    "aggregate": ("0.004", "0.05"),
    "concrete": ("0.005", "0.03"),
    "masonry": ("0.003", "0.02"),
    "finish": ("0.003", "0"),
    "electric": ("0.003", "0"),
    "pipe": ("0.003", "0"),
    "timber": ("0.003", "0.02"),
    "insul": ("0.003", "0"),
    "fastener": ("0.003", "0"),
}


@dataclass
class Sku:
    key: str
    group: str
    phase: str  # structure | finish
    names: list[str]
    base_unit: str  # единица цены base_price
    base_price: str  # сом с НДС за base_unit, октябрь 2025
    qty: tuple[float, float]  # спрос в base_unit на объект в месяц
    units: list[tuple[str, str, float]]  # (единица в учёте, сколько base_unit в 1 такой единице, вес выбора)
    suppliers: list[tuple[str, float]]  # (поставщик, доля)
    market: bool = False  # есть ли цены в госзакупках


def _u(*spec):
    return [(u, k, w) for u, k, w in spec]


CATALOG: list[Sku] = [
    *[
        Sku(
            f"rebar{d}",
            "metal",
            "structure",
            [
                f"Арматура А500С ф{d}",
                f"Арм. d{d} А500С L=11,7",
                f"Арматура {d} мм А500С (мера 11,7м)",
                f"АРМАТУРА А500С Ø{d} ГОСТ 34028-2016",
            ],
            "t",
            p,
            q,
            _u(("т", "1", 0.8), ("кг", "0.001", 0.2)),
            [("ОсОО «Темир Снаб»", 0.65), ("ОсОО «Металл Базар КГ»", 0.35)],
            market=d in (10, 12, 14),
        )
        for d, p, q in (
            (8, "74500", (3, 6)),
            (10, "73000", (6, 12)),
            (12, "71500", (18, 30)),
            (14, "71000", (10, 20)),
            (16, "70500", (6, 12)),
        )
    ],
    Sku(
        "wire",
        "metal",
        "structure",
        ["Проволока вязальная 1,2 мм", "Проволока вязальная т/о д.1,2", "Проволока вяз. 1.2мм"],
        "kg",
        "135",
        (300, 600),
        _u(("кг", "1", 1)),
        [("ОсОО «Темир Снаб»", 0.7), ("ОсОО «Металл Базар КГ»", 0.3)],
    ),
    Sku(
        "cem400",
        "cement",
        "structure",
        [
            "Цемент М400 (мешок 50кг)",
            "Портландцемент ПЦ400-Д20 50 кг",
            "Цемент м-400 Кант 50кг",
            "ЦЕМЕНТ М400 МЕШ 50 КГ",
        ],
        "t",
        "8300",
        (40, 80),
        _u(("меш", "0.05", 0.6), ("т", "1", 0.25), ("шт", "0.05", 0.15)),
        [("ОсОО «Кант Цемент Снаб»", 0.6), ("ОсОО «Цемент Опт»", 0.4)],
        market=True,
    ),
    Sku(
        "cem500",
        "cement",
        "structure",
        ["Цемент М500 навал", "Портландцемент ПЦ 500-Д0 (навал)", "Цемент м500 насыпью"],
        "t",
        "8900",
        (20, 40),
        _u(("т", "1", 1)),
        [("ОсОО «Кант Цемент Снаб»", 0.6), ("ОсОО «Цемент Опт»", 0.4)],
    ),
    Sku(
        "sand",
        "aggregate",
        "structure",
        ["Песок мытый", "Песок речной мытый", "Песок строительный мытый (м3)"],
        "m3",
        "1150",
        (150, 250),
        _u(("м3", "1", 0.7), ("куб", "1", 0.3)),
        [("ИП Жумабаев Н.", 0.75), ("ОсОО «Ала-Тоо Карьер»", 0.25)],
        market=True,
    ),
    Sku(
        "gr520",
        "aggregate",
        "structure",
        ["Щебень фр. 5-20", "Щебень 5-20 мм", "Щебень гранитный (5-20)", "ЩЕБЕНЬ ФР.5-20 ГОСТ 8267-93"],
        "m3",
        "1450",
        (120, 200),
        _u(("м3", "1", 0.7), ("куб", "1", 0.3)),
        [("ИП Жумабаев Н.", 0.75), ("ОсОО «Ала-Тоо Карьер»", 0.25)],
        market=True,
    ),
    Sku(
        "gr2040",
        "aggregate",
        "structure",
        ["Щебень фр. 20-40", "Щебень 20-40мм"],
        "m3",
        "1350",
        (60, 100),
        _u(("м3", "1", 1)),
        [("ОсОО «Ала-Тоо Карьер»", 1.0)],
    ),
    Sku(
        "conc300",
        "concrete",
        "structure",
        ["Бетон М300 (В22,5)", "Бетон товарный B22.5 П4", "БСГ М300 П4 F150 W6", "Бетон м-300"],
        "m3",
        "5200",
        (400, 700),
        _u(("м3", "1", 1)),
        [("ОсОО «Бетон Профи»", 0.55), ("ОсОО «БишкекБетон»", 0.45)],
        market=True,
    ),
    Sku(
        "conc350",
        "concrete",
        "structure",
        ["Бетон М350 (В25)", "Бетон B25 П4", "БСГ М350"],
        "m3",
        "5600",
        (200, 400),
        _u(("м3", "1", 1)),
        [("ОсОО «Бетон Профи»", 0.55), ("ОсОО «БишкекБетон»", 0.45)],
    ),
    Sku(
        "mort150",
        "concrete",
        "structure",
        ["Раствор М150", "Раствор кладочный М-150", "Раствор цем. М150"],
        "m3",
        "4300",
        (40, 80),
        _u(("м3", "1", 1)),
        [("ОсОО «Бетон Профи»", 1.0)],
    ),
    Sku(
        "brick",
        "masonry",
        "structure",
        ["Кирпич керамический полнотелый М150", "Кирпич красный полнотелый М150"],
        "pcs",
        "14.5",
        (20000, 40000),
        _u(("шт", "1", 1)),
        [("ОсОО «Кирпичный двор»", 1.0)],
        market=True,
    ),
    Sku(
        "gasblock",
        "masonry",
        "structure",
        ["Газоблок D500 600х300х200", "Газобетонный блок D500 600*300*200", "Блок газобетонный Д500 600x300x200"],
        "pcs",
        "135",
        (6000, 12000),
        _u(("шт", "1", 1)),
        [("ОсОО «Кирпичный двор»", 1.0)],
    ),
    Sku(
        "cab25",
        "electric",
        "finish",
        ["Кабель ВВГнг 3х2,5", "Кабель ВВГнг 3*2.5 (бухта 100м)", "Кабель силовой ВВГнг 3x2,5 ГОСТ 31996-2012"],
        "m",
        "98",
        (1500, 3000),
        _u(("м", "1", 1)),
        [("ОсОО «Электро Трейд»", 0.85), (RETAIL, 0.15)],
        market=True,
    ),
    Sku(
        "cab15",
        "electric",
        "finish",
        ["Кабель ВВГнг 3х1,5", "Кабель ВВГнг 3*1.5"],
        "m",
        "64",
        (1000, 2000),
        _u(("м", "1", 1)),
        [("ОсОО «Электро Трейд»", 0.85), (RETAIL, 0.15)],
    ),
    Sku(
        "pvs",
        "electric",
        "finish",
        ["Провод ПВС 2х1,5", "Провод ПВС 2*1.5 (бухта)"],
        "m",
        "38",
        (500, 1000),
        _u(("м", "1", 1)),
        [("ОсОО «Электро Трейд»", 1.0)],
    ),
    Sku(
        "ppr20",
        "pipe",
        "finish",
        ["Труба ППР PN20 ф20", "Труба PPR PN 20 д.20", "Труба полипропиленовая 20 PN20"],
        "m",
        "58",
        (800, 1500),
        _u(("м", "1", 1)),
        [("ОсОО «ТрубаСнаб»", 1.0)],
    ),
    Sku(
        "ppr25",
        "pipe",
        "finish",
        ["Труба ППР PN20 ф25", "Труба PPR PN 20 д.25"],
        "m",
        "85",
        (400, 800),
        _u(("м", "1", 1)),
        [("ОсОО «ТрубаСнаб»", 1.0)],
    ),
    Sku(
        "profpipe",
        "metal",
        "structure",
        ["Труба профильная 40х20х2", "Труба проф. 40*20*2,0", "Профтруба 40х20х2 мм"],
        "m",
        "210",
        (300, 600),
        _u(("м", "1", 1)),
        [("ОсОО «Темир Снаб»", 0.7), ("ОсОО «Металл Базар КГ»", 0.3)],
    ),
    Sku(
        "drywall",
        "finish",
        "finish",
        ["Гипсокартон 12,5 мм 1200х2500", "ГКЛ 12.5мм 1200*2500"],
        "sheet",
        "520",
        (400, 800),
        _u(("лист", "1", 0.7), ("шт", "1", 0.3)),
        [("ОсОО «Отделка Центр»", 0.85), (RETAIL, 0.15)],
        market=True,
    ),
    Sku(
        "prof6027",
        "finish",
        "finish",
        ["Профиль ПП 60х27 3м", "Профиль потолочный 60*27 (3 м)"],
        "pcs",
        "165",
        (600, 1200),
        _u(("шт", "1", 1)),
        [("ОсОО «Отделка Центр»", 0.85), (RETAIL, 0.15)],
    ),
    Sku(
        "putty",
        "finish",
        "finish",
        ["Шпаклевка гипсовая 25 кг", "Шпатлевка финишная гипс. 25кг", "Шпаклевка гипс. мешок 25 кг"],
        "kg",
        "26",
        (3000, 6000),
        _u(("меш", "25", 0.6), ("шт", "25", 0.2), ("кг", "1", 0.2)),
        [("ОсОО «Отделка Центр»", 0.85), (RETAIL, 0.15)],
        market=True,
    ),
    Sku(
        "plaster",
        "finish",
        "finish",
        ["Штукатурка гипсовая 30 кг", "Штукатурка гипс. машинная 30кг"],
        "kg",
        "19.5",
        (6000, 12000),
        _u(("меш", "30", 0.8), ("кг", "1", 0.2)),
        [("ОсОО «Отделка Центр»", 1.0)],
    ),
    Sku(
        "paint",
        "finish",
        "finish",
        ["Краска ВД интерьерная 15 кг", "Краска водоэмульсионная 15кг", "Краска в/д белая (ведро 15 кг)"],
        "kg",
        "115",
        (800, 1600),
        _u(("шт", "15", 0.5), ("ведро", "15", 0.3), ("кг", "1", 0.2)),
        [("ОсОО «Отделка Центр»", 0.85), (RETAIL, 0.15)],
    ),
    Sku(
        "primer",
        "finish",
        "finish",
        ["Грунтовка глубокого проникновения 10 л", "Грунтовка гл. проникн. 10л (канистра)"],
        "l",
        "75",
        (600, 1200),
        _u(("шт", "10", 0.6), ("кан", "10", 0.4)),
        [("ОсОО «Отделка Центр»", 1.0)],
    ),
    Sku(
        "tileglue",
        "finish",
        "finish",
        ["Клей плиточный 25 кг", "Плиточный клей 25кг", "Клей для плитки 25 кг"],
        "kg",
        "19",
        (3000, 6000),
        _u(("меш", "25", 0.8), ("кг", "1", 0.2)),
        [("ОсОО «Отделка Центр»", 1.0)],
    ),
    Sku(
        "board",
        "timber",
        "structure",
        ["Доска обрезная 50х150х6000", "Доска обр. 50*150*6000", "Пиломатериал обрезной 50x150x6000"],
        "m3",
        "19500",
        (8, 15),
        _u(("м3", "1", 0.7), ("куб", "1", 0.3)),
        [("ОсОО «Лесторг»", 0.7), ("ИП Бекова С.", 0.3)],
    ),
    Sku(
        "timber",
        "timber",
        "structure",
        ["Брус 100х100х6000", "Брус обрезной 100*100*6000"],
        "m3",
        "20500",
        (4, 8),
        _u(("м3", "1", 1)),
        [("ОсОО «Лесторг»", 0.7), ("ИП Бекова С.", 0.3)],
    ),
    Sku(
        "plywood",
        "timber",
        "structure",
        ["Фанера 18 мм 1525х1525", "Фанера ФК 18мм 1525*1525"],
        "sheet",
        "2650",
        (60, 120),
        _u(("лист", "1", 1)),
        [("ОсОО «Лесторг»", 1.0)],
    ),
    Sku(
        "minwool",
        "insul",
        "finish",
        ["Утеплитель минвата 50 мм", "Минплита 50мм", "Утеплитель минераловатный 50 мм"],
        "m2",
        "210",
        (800, 1600),
        _u(("м2", "1", 1)),
        [("ОсОО «Изоляция КГ»", 1.0)],
    ),
    Sku(
        "angle",
        "metal",
        "structure",
        ["Уголок 50х50х5", "Уголок стальной 50*50*5"],
        "m",
        "260",
        (200, 400),
        _u(("м", "1", 1)),
        [("ОсОО «Темир Снаб»", 0.7), ("ОсОО «Металл Базар КГ»", 0.3)],
    ),
    Sku(
        "profnastil",
        "insul",
        "structure",
        ["Профнастил С8 0,45 мм", "Профлист С-8 0.45мм"],
        "m2",
        "610",
        (300, 600),
        _u(("м2", "1", 1)),
        [("ОсОО «Изоляция КГ»", 1.0)],
    ),
    Sku(
        "mesh",
        "metal",
        "structure",
        ["Сетка кладочная 50х50х4", "Сетка сварная 50*50*4 мм"],
        "m2",
        "120",
        (400, 800),
        _u(("м2", "1", 1)),
        [("ОсОО «Темир Снаб»", 0.7), ("ОсОО «Металл Базар КГ»", 0.3)],
    ),
    Sku(
        "electrode",
        "fastener",
        "structure",
        ["Электроды МР-3 ф3", "Электроды MP-3 ф3 (пачка 5кг)"],
        "kg",
        "260",
        (100, 200),
        _u(("кг", "1", 1)),
        [("ОсОО «Крепёж Плюс»", 0.85), (RETAIL, 0.15)],
    ),
    Sku(
        "screw",
        "fastener",
        "finish",
        ["Саморезы 3,5х35", "Саморез по дереву 3.5*35"],
        "kg",
        "420",
        (40, 80),
        _u(("кг", "1", 1)),
        [("ОсОО «Крепёж Плюс»", 0.85), (RETAIL, 0.15)],
    ),
    Sku(
        "dowel",
        "fastener",
        "finish",
        ["Дюбель-гвоздь 6х40", "Дюбель 6*40"],
        "pcs",
        "3.5",
        (4000, 8000),
        _u(("шт", "1", 1)),
        [("ОсОО «Крепёж Плюс»", 1.0)],
    ),
]
BY_KEY = {s.key: s for s in CATALOG}

_LAT = str.maketrans("АВСЕМРТ", "ABCEMPT")


def month_idx(d: date) -> int:
    return (d.year - START.year) * 12 + d.month - START.month


def market_price(sku: Sku, d: date) -> Decimal:
    """«Честная» рыночная цена позиции на дату: база × тренд × сезон."""
    m = month_idx(d)
    trend, season = (Decimal(x) for x in TRENDS[sku.group])
    p = Decimal(sku.base_price) * (1 + trend) ** m
    if d.month in (5, 6, 7, 8, 9):
        p *= 1 + season
    return p


def supplier_markup(name: str) -> Decimal:
    return Decimal(SUPPLIERS[name][0])


@dataclass
class GenLine:
    d: date
    doc: str
    supplier: str
    sku: str
    name: str
    unit: str
    qty: Decimal  # в единице учёта
    price: Decimal  # за единицу учёта
    amount: Decimal
    obj: str
    who: str
    fair_amount: Decimal  # честная стоимость (основной поставщик без нарушений)
    anomalies: list[str] = field(default_factory=list)


def _q(v: Decimal, places: str = "0.01") -> Decimal:
    return v.quantize(Decimal(places), ROUND_HALF_UP)


class Generator:
    def __init__(self, seed: int = 11):
        self.rng = random.Random(seed)
        self.doc_no = 1000
        self.lines: list[GenLine] = []

    def _doc(self) -> str:
        self.doc_no += self.rng.randint(1, 4)
        return f"{self.doc_no:07d}"

    def _name(self, sku: Sku, who: str) -> str:
        names = sku.names
        i = WHO_INDEX[who] % len(names) if self.rng.random() < 0.8 else self.rng.randrange(len(names))
        n = names[i]
        r = self.rng.random()
        if r < 0.05:
            n = n.upper()
        elif r < 0.10:
            n = n.translate(_LAT)  # латиница в марках из копипасты
        elif r < 0.13:
            n = n.replace(" ", "  ", 1)
        return n

    def _unit(self, sku: Sku) -> tuple[str, Decimal]:
        units = sku.units
        u, k, _ = self.rng.choices(units, weights=[w for _, _, w in units])[0]
        return u, Decimal(k)

    def _supplier(self, sku: Sku, d: date, who: str) -> tuple[str, Decimal, list[str]]:
        """Поставщик, множитель цены к рынку и подсаженные нарушения строки."""
        m = month_idx(d)
        sups = sku.suppliers
        name = self.rng.choices([s for s, _ in sups], weights=[w for _, w in sups])[0]
        mult = supplier_markup(name)
        tags: list[str] = []
        # A1: «свой» поставщик снабженца по арматуре ⌀12/⌀14 с 4-го месяца
        if sku.key in ("rebar12", "rebar14") and m >= 3 and self.rng.random() < 0.45:
            name, mult, tags = "ИП Сыдыков А.Т.", Decimal("1.15"), ["A1"]
        # A3: песок и щебень 5-20 у основного поставщика на 22% выше рынка
        elif name == "ИП Жумабаев Н.":
            mult, tags = Decimal("1.22"), ["A3"]
        # A2: ползучий рост шпаклёвки и ГКЛ у «Отделка Центр» с 3-го месяца
        elif name == "ОсОО «Отделка Центр»" and sku.key in ("putty", "drywall") and m >= 2:
            mult, tags = Decimal("1") + Decimal("0.025") * (m - 1), ["A2"]
        # A8: розница вместо опта
        elif name == RETAIL:
            tags = ["A8"]
        return name, mult, tags

    def _fair_mult(self, sku: Sku) -> Decimal:
        """Честная цена = основной (самый массовый) не-аномальный поставщик позиции."""
        normal = [(s, w) for s, w in sku.suppliers if s not in (RETAIL, "ИП Жумабаев Н.")]
        main = max(normal, key=lambda x: x[1])[0] if normal else "ОсОО «Ала-Тоо Карьер»"
        return supplier_markup(main)

    def add(
        self,
        sku: Sku,
        d: date,
        qty_base: Decimal,
        obj: str,
        who: str,
        supplier: str | None = None,
        mult: Decimal | None = None,
        tags: list[str] | None = None,
        unit: tuple[str, Decimal] | None = None,
        doc: str | None = None,
    ) -> GenLine:
        if supplier is None:
            supplier, mult, tags = self._supplier(sku, d, who)
        unit_name, per_unit = unit or self._unit(sku)
        mkt = market_price(sku, d)
        jitter = Decimal(str(round(self.rng.uniform(0.985, 1.015), 4)))
        price_base = mkt * mult * jitter
        whole = per_unit != 1 or unit_name in ("шт", "лист", "м")
        qty = _q(qty_base / per_unit, "1") if whole else _q(qty_base, "0.001")
        if qty == 0:
            qty = Decimal("1")
        price = _q(price_base * per_unit)
        amount = _q(price * qty)
        fair_amount = _q(mkt * self._fair_mult(sku) * jitter * per_unit * qty)
        line = GenLine(
            d,
            doc or self._doc(),
            supplier,
            sku.key,
            self._name(sku, who),
            unit_name,
            qty,
            price,
            amount,
            obj,
            who,
            fair_amount,
            list(tags or []),
        )
        self.lines.append(line)
        return line

    def generate(self) -> list[GenLine]:
        rng = self.rng
        for obj, phases in OBJECTS.items():
            for m in range(MONTHS):
                month_start = date(START.year + (START.month - 1 + m) // 12, (START.month - 1 + m) % 12 + 1, 1)
                for sku in CATALOG:
                    if m not in phases[sku.phase]:
                        continue
                    demand = Decimal(str(round(rng.uniform(*sku.qty), 3)))
                    k = rng.choices([1, 2, 3], weights=[0.35, 0.45, 0.2])[0]
                    for _ in range(k):
                        d = month_start + timedelta(days=rng.randrange(0, 27))
                        self.add(sku, d, demand / k, obj, WHO[sku.group])
        self._seed_split()
        self._seed_duplicates()
        self._seed_unit_errors()
        self.lines.sort(key=lambda l: (l.d, l.doc))
        return self.lines

    def _seed_split(self) -> None:
        """A4: цемент М400 дробится на 4 покупки по 420–495 тыс. сом (чуть ниже порога 500 тыс.) за 5 дней."""
        sku = BY_KEY["cem400"]
        for n, start in enumerate((date(2026, 3, 9), date(2026, 5, 18), date(2026, 7, 13))):
            tag = f"A4-{n + 1}"
            for i in range(4):
                d = start + timedelta(days=[0, 1, 3, 4][i])
                target = Decimal(self.rng.randrange(420000, 495000))
                qty_base = target / market_price(sku, d)
                self.add(
                    sku,
                    d,
                    qty_base,
                    "ЖК «Ордо», Бишкек",
                    "Асанов Б.",
                    supplier="ОсОО «Цемент Опт»",
                    mult=supplier_markup("ОсОО «Цемент Опт»"),
                    tags=[tag],
                    unit=("т", Decimal("1")),
                )

    def _seed_duplicates(self) -> None:
        """A5: строка документа внесена повторно (тот же номер, дата, сумма)."""
        cands = [l for l in self.lines if l.sku in ("conc300", "rebar16", "cab15") and not l.anomalies]
        for n, src in enumerate(self.rng.sample(cands, 3)):
            dup = GenLine(
                src.d,
                src.doc,
                src.supplier,
                src.sku,
                src.name,
                src.unit,
                src.qty,
                src.price,
                src.amount,
                src.obj,
                src.who,
                Decimal("0"),
                [f"A5-{n + 1}"],
            )
            self.lines.append(dup)

    def _seed_unit_errors(self) -> None:
        """A6: цена за кг записана в строке с единицей «т» (сумма верная → цена за т в 1000 раз меньше)."""
        cands = [l for l in self.lines if l.sku in ("rebar10", "rebar16") and l.unit == "т" and not l.anomalies]
        for n, l in enumerate(self.rng.sample(cands, 2)):
            l.qty = _q(l.qty * 1000, "1")  # количество в кг, а единица осталась «т»
            l.price = _q(l.amount / l.qty)
            l.anomalies.append(f"A6-{n + 1}")


def write_xlsx(lines: list[GenLine], path: Path) -> dict[str, GenLine]:
    """Пишет выгрузку «как из 1С»: шапка отчёта, затем таблица. Возвращает line_id → строка."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Закупки"
    ws.append(["Закупки материалов (поступление товаров и услуг)"])
    ws.append([f"Период: {START:%d.%m.%Y} - {date(2026, 9, 30):%d.%m.%Y}"])
    ws.append([f"Организация: {COMPANY}"])
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
    by_id: dict[str, GenLine] = {}
    for l in lines:
        vat = _q(l.amount * VAT_RATE_KG / (1 + VAT_RATE_KG))
        ws.append(
            [
                l.d,
                f"Поступление товаров и услуг {l.doc} от {l.d:%d.%m.%Y}",
                l.supplier,
                SUPPLIERS[l.supplier][1],
                l.name,
                l.unit,
                float(l.qty),
                float(l.price),
                float(l.amount),
                float(vat),
                l.obj,
                l.who,
            ]
        )
        by_id[f"r{ws.max_row}"] = l
    for col, w in zip("ABCDEFGHIJKL", (11, 46, 30, 16, 44, 8, 11, 11, 13, 11, 22, 16), strict=True):
        ws.column_dimensions[col].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return by_id


def market_points(seed: int = 7) -> list[dict]:
    """Рыночные цены «как из госзакупок»: 2–4 точки в месяц на позицию с шумом ±5%."""
    rng = random.Random(seed)
    out = []
    for sku in CATALOG:
        if not sku.market:
            continue
        for m in range(MONTHS):
            for _ in range(rng.randint(2, 4)):
                d = date(START.year + (START.month - 1 + m) // 12, (START.month - 1 + m) % 12 + 1, rng.randint(1, 27))
                noise = Decimal(str(round(max(0.9, min(1.1, rng.gauss(1.0, 0.05))), 4)))
                out.append(
                    {
                        "sku_key": sku.key,
                        "name": sku.names[0],
                        "date": d.isoformat(),
                        "unit": sku.units[0][0],
                        "per_unit": sku.units[0][1],
                        "price": str(_q(market_price(sku, d) * noise * Decimal(sku.units[0][1]))),
                        "source": rng.choice(["synthetic:goszakupki_plan", "synthetic:goszakupki_bid"]),
                    }
                )
    return out


def build(out_dir: Path, seed: int = 11) -> dict:
    gen = Generator(seed)
    lines = gen.generate()
    xlsx = out_dir / "registry.xlsx"
    by_id = write_xlsx(lines, xlsx)

    truth_lines = {}
    instances: dict[str, dict] = {}
    for lid, l in by_id.items():
        over = l.amount if any(t.startswith("A5") for t in l.anomalies) else max(Decimal("0"), l.amount - l.fair_amount)
        if not any(t in ("A1", "A2", "A3", "A8") or t.startswith("A5") for t in l.anomalies):
            over = Decimal("0")
        truth_lines[lid] = {"sku": l.sku, "anomalies": l.anomalies, "true_over": str(_q(over))}
        for t in l.anomalies:
            inst_id = t if "-" in t else f"{t}:{l.sku}:{l.supplier}"
            inst = instances.setdefault(inst_id, {"type": t.split("-")[0], "line_ids": [], "true_over": Decimal("0")})
            inst["line_ids"].append(lid)
            inst["true_over"] += over
    rule_of = {"A1": "P-01", "A2": "P-02", "A3": "P-03", "A4": "P-05", "A5": "P-06", "A6": "P-07", "A8": "P-01"}
    truth = {
        "company": COMPANY,
        "lines": truth_lines,
        "instances": {
            k: {**v, "rule": rule_of[v["type"]], "true_over": str(_q(v["true_over"]))} for k, v in instances.items()
        },
        "approval_threshold": str(APPROVAL_THRESHOLD),
    }
    (out_dir / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out_dir / "market_raw.jsonl").open("w", encoding="utf-8") as f:
        for p in market_points():
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return {"lines": len(lines), "instances": len(instances), "xlsx": str(xlsx)}
