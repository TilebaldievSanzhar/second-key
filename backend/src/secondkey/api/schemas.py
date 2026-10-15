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


class PreviewOut(BaseModel):
    header_row: int
    columns: list[str]
    mapping: dict[str, int]
    sample: list[list[str]]
    missing: list[str]


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
    spend: Decimal
    overpayment: Decimal
    lines: int


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
    answered_at: datetime | None
    decision_note: str | None
    decided_at: datetime | None
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
    price_base: Decimal | None
    norm_status: str
    norm_note: str | None
    overpayment: Decimal


class FindingLineOut(LineOut):
    finding_overpayment: Decimal


class PersonRef(BaseModel):
    id: uuid.UUID
    full_name: str


class FindingDetail(FindingOut):
    evidence: dict[str, Any]
    lines: list[FindingLineOut]
    answered_by: PersonRef | None = None
    decided_by: PersonRef | None = None


class AnswerIn(BaseModel):
    answer: str = Field(min_length=3, max_length=4000)


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


class PriceHistory(BaseModel):
    sku: str
    label: str | None
    unit: str | None
    purchases: list[PricePoint]
    market: list[PricePoint]
