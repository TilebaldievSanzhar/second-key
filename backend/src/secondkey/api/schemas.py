"""Контракты API. Деньги и цены — строки-десятичные («125000.00»): без потерь float на клиенте."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from secondkey.db.models import FindingStatus, Role, RunStatus


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth / org -------------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class OrgRef(Out):
    id: uuid.UUID
    name: str


class MembershipOut(Out):
    org: OrgRef
    role: Role
    alias: str | None = None


class UserOut(Out):
    id: uuid.UUID
    email: str
    full_name: str


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


class OrgSettings(BaseModel):
    """Политика закупок компании — пороги правил аудита."""

    approval_threshold: Decimal = Field(gt=0, description="Порог согласования закупки, сом")
    parallel_window_days: int = Field(ge=7, le=120, description="Окно сравнения с параллельными поставщиками, дней")
    parallel_tolerance: Decimal = Field(ge=0, le=Decimal("0.5"), description="Допуск к цене другого поставщика, доля")
    market_tolerance: Decimal = Field(ge=0, le=Decimal("0.5"), description="Допуск к рынку, доля")
    market_window_days: int = Field(ge=7, le=180, description="Окно поиска рыночных цен, дней")


class OrgSettingsPatch(BaseModel):
    approval_threshold: Decimal | None = Field(default=None, gt=0)
    parallel_window_days: int | None = Field(default=None, ge=7, le=120)
    parallel_tolerance: Decimal | None = Field(default=None, ge=0, le=Decimal("0.5"))
    market_tolerance: Decimal | None = Field(default=None, ge=0, le=Decimal("0.5"))
    market_window_days: int | None = Field(default=None, ge=7, le=180)


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    currency: str
    settings: OrgSettings
    network_opt_in: bool = False  # обезличенный обмен ценами с другими клиентами (services/network.py)


class NetworkIn(BaseModel):
    opt_in: bool


class CapabilitiesOut(BaseModel):
    """Что настроено на сервере."""

    secrets: bool  # SK_DATA_KEY: можно хранить пароли подключений 1С
    llm: bool  # SK_LLM_API_KEY: ИИ-подсказки
    public_url: str
    registry: str  # агрегатор реестров («statsnet») или пусто — только ручная проверка


class MemberOut(BaseModel):
    user: UserOut
    role: Role
    alias: str | None


class MemberIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    alias: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=10, max_length=200)


# --- uploads / runs ---------------------------------------------------------------------------


class SuggestedMapping(BaseModel):
    header_row: int | None = None
    mapping: dict[str, int]
    by: Literal["saved", "ai"]
    notes: str = ""


class PreviewOut(BaseModel):
    header_row: int
    columns: list[str]
    mapping: dict[str, int]
    sample: list[list[str]]
    missing: list[str]
    data_rows: int = 0
    suggested: SuggestedMapping | None = None


class UploadOut(Out):
    id: uuid.UUID
    filename: str
    size: int
    created_at: datetime
    preview: PreviewOut


class RunIn(BaseModel):
    upload_id: uuid.UUID
    mapping: dict[str, int] | None = Field(
        default=None, description="Подтверждённое сопоставление: поле → индекс колонки"
    )
    header_row: int | None = Field(default=None, ge=1)


class RunOut(Out):
    id: uuid.UUID
    status: RunStatus
    progress: int
    error: str | None
    filename: str
    period_start: date | None
    period_end: date | None
    lines_total: int
    lines_ok: int
    lines_needs_input: int
    spend: Decimal
    overpayment: Decimal
    findings_count: int
    market_points: int
    warnings: list[str]
    params: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BreakdownRow(BaseModel):
    key: str
    label: str
    spend: Decimal = Field(description="Оборот разреза, сом (для правил — 0)")
    overpayment: Decimal = Field(description="Возможная переплата без двойного счёта, сом")
    lines: int = Field(description="Строк учёта с переплатой")
    findings: int | None = Field(default=None, description="Вопросов по правилу (только в разрезе по правилам)")


class FindingCounts(BaseModel):
    open: int = 0
    answered: int = 0
    confirmed: int = 0
    justified: int = 0
    data_error: int = 0


class RunSummary(BaseModel):
    run: RunOut
    findings: FindingCounts
    confirmed_overpayment: Decimal
    by_rule: list[BreakdownRow]
    by_supplier: list[BreakdownRow]
    by_responsible: list[BreakdownRow]
    by_material: list[BreakdownRow]


# --- findings ---------------------------------------------------------------------------------


class FindingOut(Out):
    id: uuid.UUID
    run_id: uuid.UUID
    code: str
    severity: str
    title: str
    message: str
    sku: str | None
    sku_label: str | None
    supplier: str | None
    responsible: str | None
    reference: Decimal | None
    reference_kind: str | None
    overpayment: Decimal
    status: FindingStatus
    answer: str | None
    answer_reasons: list[str] = []
    answer_review: dict[str, Any] | None = None
    answered_at: datetime | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime
    lines_count: int = 0


class LineOut(Out):
    id: uuid.UUID
    source_row: int | None
    date: date
    doc_no: str | None
    supplier: str
    item_raw: str
    qty: Decimal
    unit_raw: str | None
    price: Decimal
    amount: Decimal
    object_name: str | None
    responsible: str | None
    sku: str | None
    sku_label: str | None
    unit_base: str | None
    qty_base: Decimal | None
    price_base: Decimal | None
    norm_status: str
    norm_note: str | None
    overpayment: Decimal
    doc_in_no: str | None = None
    doc_in_date: date | None = None


class FindingLineOut(LineOut):
    finding_overpayment: Decimal


class SupplierBuyer(BaseModel):
    name: str
    spend: Decimal
    lines: int


class SupplierTrace(BaseModel):
    code: str  # L-01 … L-12; P-01/P-02/P-03 — ценовые находки по строкам пары
    group: str  # price | behavior | identity
    text: str
    responsible: str | None = None
    money: Decimal = Decimal("0")
    facts: dict[str, Any] = {}


class SupplierCardOut(BaseModel):
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    bank_account: str | None = None


class SupplierOut(Out):
    """Досье поставщика за прогон: видят собственник и финансы."""

    id: uuid.UUID
    name: str
    inn: str | None
    inn_kind: str | None
    inn_date: date | None
    spend: Decimal
    lines: int
    share: Decimal
    first_date: date
    last_date: date
    skus: int
    buyers: list[SupplierBuyer]
    card: SupplierCardOut | None
    traces: list[SupplierTrace]
    linked: bool


class LinkOut(BaseModel):
    """Связка — находка LINK: пара «снабженец — поставщик» и сошедшиеся следы."""

    finding_id: uuid.UUID
    supplier: str | None
    supplier_id: uuid.UUID | None
    responsible: str | None
    status: FindingStatus
    severity: str
    overpayment: Decimal  # справочно: ценовые вопросы по строкам пары, в итог уже вошли
    sku_label: str | None
    pair_spend: Decimal
    pair_share: Decimal
    groups: list[str]
    traces: list[SupplierTrace]


class MatrixCell(BaseModel):
    supplier: str
    supplier_id: uuid.UUID | None
    buyer: str | None  # None — строки без ответственного
    spend: Decimal
    lines: int
    share: Decimal  # доля оборота поставщика через этого снабженца
    codes: list[str]  # следы пары (L-01, L-04, L-05, L-06)
    linked: bool  # по паре задан вопрос LINK


class LinkBuyer(BaseModel):
    name: str | None
    spend: Decimal
    lines: int
    suppliers: int
    links: int


class LinkSupplier(BaseModel):
    id: uuid.UUID
    name: str
    spend: Decimal
    share: Decimal
    inn_kind: str | None
    inn_date: date | None
    linked: bool
    groups: list[str]
    traces: list[SupplierTrace]


class LinksOverview(BaseModel):
    """Раздел «Связи»: снабженцы, поставщики, матрица оборотов и вопросы-связки."""

    suppliers_total: int
    buyers: list[LinkBuyer]
    suppliers: list[LinkSupplier]
    cells: list[MatrixCell]
    links: list[LinkOut]


class SupplierItem(BaseModel):
    sku: str
    label: str
    unit: str | None
    lines: int
    spend: Decimal
    price: Decimal | None  # медиана цены за базовую единицу у этого поставщика
    others: Decimal | None  # у других поставщиков в те же месяцы
    others_suppliers: int
    market: Decimal | None  # госзакупки в те же месяцы
    market_points: int
    vs_others: Decimal | None
    vs_market: Decimal | None


class SharedRequisite(BaseModel):
    other: str | None
    other_id: uuid.UUID | None
    fields: list[str]


class RegistryOut(BaseModel):
    """Сведения реестра о поставщике: ручная проверка по выписке или агрегатор."""

    inn: str
    source: str  # manual | statsnet
    name: str | None = None
    status: str | None = None
    active: bool | None = None
    registered: date | None = None
    director: str | None = None
    founders: list[str] = []
    address: str | None = None
    unreliable: bool | None = None
    risks: list[str] = []
    checked_at: date | None = None
    url: str | None = None


class RegistryIn(BaseModel):
    """Ручная проверка по выписке Минюста (record.minjust.gov.kg) или другому источнику."""

    status: str | None = Field(
        default=None, max_length=120, description="Как в выписке: «Действующее», «Ликвидировано»"
    )
    active: bool | None = None
    registered: date | None = None
    director: str | None = Field(default=None, max_length=200)
    founders: list[str] = Field(default=[], max_length=30)
    address: str | None = Field(default=None, max_length=500)
    unreliable: bool | None = Field(default=None, description="В реестре недобросовестных поставщиков госзакупок")
    url: str | None = Field(default=None, max_length=500, description="Ссылка на выписку или источник")


class PaymentProfileOut(BaseModel):
    paid: Decimal
    delivered: Decimal
    payments: int
    prepay_share: Decimal | None
    delay_days: Decimal | None
    advance: Decimal
    advance_since: date | None
    unpaid: Decimal


class PaymentOut(Out):
    date: date
    amount: Decimal
    doc_no: str | None
    purpose: str | None


class SupplierDetail(SupplierOut):
    items: list[SupplierItem]
    shared: list[SharedRequisite]
    registry: RegistryOut | None = None  # сведения сейчас: своя ручная проверка или свежий агрегатор
    payment: PaymentProfileOut | None = None
    payments_list: list[PaymentOut] = []


class AltQuote(BaseModel):
    price: Decimal | None
    lines: int
    last_date: date
    recent: bool  # покупки за последние три месяца; иначе — последние три цены


class AltOption(AltQuote):
    supplier: str
    delta: Decimal | None  # к цене поставщика вопроса: −0,12 — на 12% дешевле


class AltMarket(BaseModel):
    price: Decimal | None
    points: int
    last_date: date
    recent: bool
    delta: Decimal | None


class AltGroup(BaseModel):
    sku: str
    label: str
    unit: str | None
    supplier: str | None
    current: AltQuote | None
    options: list[AltOption]
    market: AltMarket | None


class AlternativesOut(BaseModel):
    """«Где дешевле» по главным позициям вопроса: ваша история и госзакупки."""

    window_start: date
    window_end: date
    groups: list[AltGroup]


class PersonRef(BaseModel):
    id: uuid.UUID
    full_name: str


class FindingDetail(FindingOut):
    evidence: dict[str, Any]
    lines: list[FindingLineOut]
    answered_by: PersonRef | None = None
    decided_by: PersonRef | None = None


ANSWER_REASONS = (
    "Срочная поставка",
    "Доставка включена в цену",
    "Другая марка или качество",
    "Отсрочка платежа",
    "Ошибка в учёте",
    "Другое",
)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=3, max_length=4000)
    reasons: list[Literal[ANSWER_REASONS]] = Field(default=[], max_length=len(ANSWER_REASONS))  # type: ignore[valid-type]


class DecisionIn(BaseModel):
    status: Literal[FindingStatus.confirmed, FindingStatus.justified, FindingStatus.data_error]
    note: str | None = Field(default=None, max_length=4000)


class Page[T](BaseModel):
    items: list[T]
    total: int


# --- price history ----------------------------------------------------------------------------


class PricePoint(BaseModel):
    date: date
    price: Decimal
    supplier: str | None = None
    source: str
    ref: str | None = None
    flagged: bool = False
    line_id: uuid.UUID | None = None
    qty: Decimal | None = Field(default=None, description="Количество в базовой единице (покупки)")
    overpayment: Decimal | None = Field(default=None, description="Переплата строки, сом (покупки)")
    object: str | None = None


class PriceHistory(BaseModel):
    sku: str
    label: str | None
    unit: str | None
    purchases: list[PricePoint]
    market: list[PricePoint]


# --- интеграции ----------------------------------------------------------------------------------


class DigestOut(BaseModel):
    text: str
    by: Literal["ai", "template"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Literal["push", "mcp"]] = Field(min_length=1)


class TokenOut(Out):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str = Field(description="Показывается один раз")


class SubmitOut(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    lines: int
    status_url: str


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(
        min_length=8, max_length=500, description="Адрес публикации базы, напр. https://1c.example.kg/bp"
    )
    username: str = Field(max_length=200)
    password: str | None = Field(default=None, max_length=200, description="Пустой — оставить сохранённый")
    profile: Literal["bp30", "ut11"] = "bp30"
    period_days: int = Field(default=90, ge=7, le=730)
    schedule: Literal["manual", "daily"] = "manual"


class ConnectionOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    enabled: bool
    base_url: str | None
    username: str | None
    profile: str | None
    period_days: int
    schedule: str
    has_password: bool
    last_sync_at: datetime | None
    last_status: str | None
    last_error: str | None
    last_run_id: uuid.UUID | None


class ConnectionTestOut(BaseModel):
    ok: bool
    profile: str | None = None
    title: str | None = None
    error: str | None = None


class SyncIn(BaseModel):
    start: date | None = None
    end: date | None = None


class TimelineMonth(BaseModel):
    month: str = Field(description="ГГГГ-ММ")
    spend: Decimal
    overpayment: Decimal
    by_rule: dict[str, Decimal] = Field(description="Переплата месяца по правилам без двойного счёта")
    lines: int
    flagged_lines: int
