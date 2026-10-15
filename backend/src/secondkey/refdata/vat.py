"""НДС РК: ставка по дате документа.

Данные, не код: приведение цен всегда берёт ставку на дату КП/протокола —
исторические документы 2024–2025 жили при 12%.

Источник: новый НК РК с 01.01.2026 — базовая ставка 16% (пониженные 5%/10%
для отдельных категорий — лекарства и т.п., в стройматериалах не встречаются).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# (действует с, базовая ставка)
_VAT_RATES: list[tuple[date, Decimal]] = [
    (date(2026, 1, 1), Decimal("0.16")),
    (date(2009, 1, 1), Decimal("0.12")),
]


def vat_rate_kz(on_date: date) -> Decimal:
    for since, rate in _VAT_RATES:
        if on_date >= since:
            return rate
    raise ValueError(f"нет данных о ставке НДС РК на {on_date}")


# НДС КР: стандартная ставка 12% (Налоговый кодекс КР); пониженные ставки
# в стройматериалах не встречаются. Изменения — новой строкой таблицы.
_VAT_RATES_KG: list[tuple[date, Decimal]] = [
    (date(2009, 1, 1), Decimal("0.12")),
]


def vat_rate_kg(on_date: date) -> Decimal:
    for since, rate in _VAT_RATES_KG:
        if on_date >= since:
            return rate
    raise ValueError(f"нет данных о ставке НДС КР на {on_date}")


def vat_rate(jurisdiction: str, on_date: date) -> Decimal:
    """Ставка НДС по юрисдикции: 'KZ' | 'KG'."""
    return {"KZ": vat_rate_kz, "KG": vat_rate_kg}[jurisdiction](on_date)
