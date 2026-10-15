"""Погонные массы и переводы единиц для металлопроката.

Табличные данные ГОСТ — готовятся один раз, используются детерминированным
движком приведения цен (т ↔ м ↔ шт). LLM к этим числам не прикасается.
"""

from __future__ import annotations

from decimal import Decimal

# ГОСТ 5781-82 / ГОСТ 34028-2016: арматура, номинальный диаметр → кг/м
REBAR_KG_PER_M: dict[int, Decimal] = {
    6: Decimal("0.222"),
    8: Decimal("0.395"),
    10: Decimal("0.617"),
    12: Decimal("0.888"),
    14: Decimal("1.210"),
    16: Decimal("1.580"),
    18: Decimal("2.000"),
    20: Decimal("2.470"),
    22: Decimal("2.980"),
    25: Decimal("3.850"),
    28: Decimal("4.830"),
    32: Decimal("6.310"),
    36: Decimal("7.990"),
    40: Decimal("9.870"),
}

# Стандартные меры стержней, м (для перевода шт ↔ т)
REBAR_STANDARD_LENGTHS_M = (Decimal("11.7"), Decimal("12.0"), Decimal("6.0"))


def rebar_tonnes_to_meters(tonnes: Decimal, diameter_mm: int) -> Decimal:
    """Тонны → метры для арматуры данного диаметра."""
    kg_per_m = REBAR_KG_PER_M.get(diameter_mm)
    if kg_per_m is None:
        raise KeyError(f"нет погонной массы для арматуры ⌀{diameter_mm}")
    return tonnes * Decimal("1000") / kg_per_m


def rebar_meters_to_tonnes(meters: Decimal, diameter_mm: int) -> Decimal:
    kg_per_m = REBAR_KG_PER_M.get(diameter_mm)
    if kg_per_m is None:
        raise KeyError(f"нет погонной массы для арматуры ⌀{diameter_mm}")
    return meters * kg_per_m / Decimal("1000")


def rebar_pieces_to_tonnes(pieces: int, diameter_mm: int, length_m: Decimal) -> Decimal:
    return rebar_meters_to_tonnes(Decimal(pieces) * length_m, diameter_mm)
