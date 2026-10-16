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
    doc_in_no: str | None = None  # входящий номер — номер счёта/накладной поставщика
    doc_in_date: date | None = None


@dataclass
class SupplierCard:
    """Реквизиты поставщика из карточки контрагента 1С (или колонок таблицы)."""

    name: str
    inn: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    bank_account: str | None = None


@dataclass
class StaffRef:
    """Сотрудник: как записан в учёте и хэш ИНН (inn.inn_hash) — сами ИНН сотрудников не передаются."""

    name: str
    inn_hash: str | None = None
    role: str | None = None  # должность — для текста («прораб»)


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
    code: str  # P-01 … P-07; LINK — связка снабженца и поставщика
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
class Trace:
    """След связки: цена, поведение или личность поставщика (docs/product/SUPPLIER_LINKS.md § 2–3)."""

    code: str  # L-01 … L-12; P-01/P-02/P-03 — ценовые находки по строкам связки
    group: str  # price | behavior | identity
    text: str  # для собственника: что увидели, с цифрами
    supplier: str
    responsible: str | None = None  # для следов пары «снабженец — поставщик»
    money: Decimal = Decimal("0")
    line_ids: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)


@dataclass
class SupplierProfile:
    """Досье поставщика за прогон: обороты, через кого покупали, что известно по ИНН и реквизитам, следы."""

    name: str
    inn: str | None
    inn_kind: str | None  # org | person
    inn_date: date | None  # регистрация юрлица / рождение физлица — по номеру ИНН
    spend: Decimal
    lines: int
    share: Decimal  # доля в закупках прогона
    first_date: date
    last_date: date
    buyers: list[dict]  # [{"name", "spend", "lines"}] по убыванию
    skus: int
    card: SupplierCard | None = None
    traces: list[Trace] = field(default_factory=list)
    payment: dict | None = None  # как платят: engine/payments.PaymentProfile.as_dict()
    registry: dict | None = None  # сведения реестра, использованные в прогоне


@dataclass
class AuditResult:
    company: str
    period: tuple[date, date] | None
    lines: list[NormLine]
    findings: list[AuditFinding]
    line_overpayment: dict[str, Decimal]  # итог по строке = максимум по правилам (без двойного счёта)
    warnings: list[str] = field(default_factory=list)
    suppliers: list[SupplierProfile] = field(default_factory=list)

    @property
    def total_spend(self) -> Decimal:
        return sum((nl.line.amount for nl in self.lines), Decimal("0"))

    @property
    def total_overpayment(self) -> Decimal:
        return sum(self.line_overpayment.values(), Decimal("0"))
