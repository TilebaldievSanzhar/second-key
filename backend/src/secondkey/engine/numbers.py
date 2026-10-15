"""Детерминированный парсинг чисел «как напечатано» → Decimal.

LLM переписывает числа строкой (как в документе), код превращает их в Decimal.
Денежная арифметика никогда не проходит через модель.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Первый числовой токен в строке: цифры + пробельные/тысячные разделители + , .
# Валютные хвосты любого вида («₸», «Т» из vision-транскрипции, «тг», «KZT», «дн.»)
# отпадают сами — берётся только числовая часть.
_NUMBER_TOKEN_RE = re.compile(r"-?\d[\d\s  .,]*")
_SPACES_RE = re.compile(r"[\s  ]")


def parse_decimal(raw: str | None) -> Decimal | None:
    """«1 234 120.50 ₸» / «36 762.00 Т» / «12 дн.» → Decimal; мусор → None (не догадка)."""
    if raw is None:
        return None
    m = _NUMBER_TOKEN_RE.search(raw)
    if not m:
        return None
    s = _SPACES_RE.sub("", m.group(0)).rstrip(".,")
    if not s or not any(ch.isdigit() for ch in s):
        return None
    if "," in s and "." in s:
        # «1,234.50» — запятые разделяют тысячи
        s = s.replace(",", "")
    elif "," in s:
        # «1234,50» — десятичная запятая
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_int(raw: str | None) -> int | None:
    d = parse_decimal(raw)
    if d is None:
        return None
    try:
        return int(d)
    except (ValueError, OverflowError):
        return None
