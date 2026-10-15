"""Модели аудита закупок: строка реестра, нормализованная строка, finding, результат.

Деньги — только Decimal. Строка реестра хранит то, что написано в учёте
(номенклатура, единица, цена «как есть»); нормализованная строка — то же в базовой
единице позиции, посчитанное кодом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class PurchaseLine:
    """Строка реестра закупок (выгрузка 1С / Excel)."""

    line_id: str  # стабильный id строки в источнике, напр. "r42"
    date: date
    supplier: str
    item_raw: str  # номенклатура как в учёте
    qty: Decimal
    unit_raw: str | None
    price: Decimal  # цена за единицу как в учёте
    amount: Decimal  # сумма строки
    doc_no: str | None = None
    supplier_inn: str | None = None
    vat_included: bool | None = None
    object_name: str | None = None  # объект / склад
    responsible: str | None = None  # снабженец / автор документа
    source_row: int | None = None  # номер строки в файле — для доказательства


def doc_short(doc_no: str | None) -> str:
    """«Поступление товаров и услуг 0001990 от 02.05.2026» → «№ 0001990»; иначе как есть."""
    import re

    if not doc_no:
        return "без номера"
    m = re.search(r"(?<![\d.])(\d{3,})(?![\d.])", doc_no)
    return f"№ {m.group(1)}" if m and len(doc_no) > len(m.group(1)) + 2 else doc_no


@dataclass
class NormLine:
    """Строка после нормализации: позиция каталога + цена за базовую единицу."""

    line: PurchaseLine
    sku: str | None  # подпись позиции: «арматура|d=12|mark=А500С»
    sku_label: str | None  # человекочитаемое имя позиции
    head: str | None  # материал: «арматура», «цемент»…
    unit: str | None  # базовая единица позиции (t, m3, pcs…)
    qty: Decimal | None  # количество в базовой единице
    price: Decimal | None  # цена за базовую единицу
    status: str = "ok"  # ok | needs_input | unit_error
    note: str = ""

    @property
    def line_id(self) -> str:
        return self.line.line_id


@dataclass
class AuditFinding:
    code: str  # P-01 … P-08
    severity: str  # high | medium | info
    title: str
    message: str
    line_ids: list[str]
    overpayment: Decimal = Decimal("0")  # оценка переплаты, сом; 0 — структурный сигнал
    sku: str | None = None
    sku_label: str | None = None
    supplier: str | None = None
    reference: Decimal | None = None  # эталон цены за базовую единицу
    reference_kind: str | None = None  # parallel | market | creep | duplicate
    evidence: dict = field(default_factory=dict)
    line_overpayment: dict[str, Decimal] = field(default_factory=dict)  # вклад каждой строки


@dataclass
class AuditResult:
    company: str
    period: tuple[date, date] | None
    lines: list[NormLine]
    findings: list[AuditFinding]
    line_overpayment: dict[str, Decimal]  # итог по строке = максимум по правилам (без двойного счёта)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_spend(self) -> Decimal:
        return sum((nl.line.amount for nl in self.lines), Decimal("0"))

    @property
    def total_overpayment(self) -> Decimal:
        return sum(self.line_overpayment.values(), Decimal("0"))
