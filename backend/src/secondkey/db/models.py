"""Модели данных. Все предметные таблицы несут org_id — граница арендатора."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from secondkey.db.base import Base, CreatedMixin, IdMixin, Money


def _enum(e: type[enum.Enum]) -> Enum:
    # строковые значения без нативного ENUM Postgres: новые статусы не требуют ALTER TYPE
    return Enum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Role(enum.StrEnum):
    owner = "owner"  # собственник: решения по находкам, настройки, пользователи
    finance = "finance"  # финансовый директор: как owner, без управления пользователями
    accountant = "accountant"  # бухгалтер: загрузка выгрузок, запуск аудита
    buyer = "buyer"  # снабжение: ответы на вопросы по своим закупкам
    viewer = "viewer"  # только чтение


class RunStatus(enum.StrEnum):
    queued = "queued"
    importing = "importing"
    normalizing = "normalizing"
    analyzing = "analyzing"
    reporting = "reporting"
    done = "done"
    failed = "failed"


class FindingStatus(enum.StrEnum):
    open = "open"  # вопрос задан
    answered = "answered"  # снабжение ответило
    confirmed = "confirmed"  # собственник: переплата подтверждена
    justified = "justified"  # собственник: отклонение обосновано
    data_error = "data_error"  # ошибка учёта, не переплата


class Organization(IdMixin, CreatedMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    jurisdiction: Mapped[str] = mapped_column(String(2), default="KG")
    currency: Mapped[str] = mapped_column(String(3), default="KGS")
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class User(IdMixin, CreatedMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user", lazy="selectin")


class Membership(IdMixin, CreatedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role))
    # как сотрудник записан в учёте («Асанов Б.»): снабженец видит вопросы по своим закупкам
    alias: Mapped[str | None] = mapped_column(String(200))

    user: Mapped[User] = relationship(back_populates="memberships")
    org: Mapped[Organization] = relationship(lazy="joined")


class Upload(IdMixin, CreatedMixin, Base):
    __tablename__ = "uploads"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="registry")
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(400))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    preview: Mapped[dict[str, Any]] = mapped_column(default=dict)  # найденная шапка и первые строки


class AuditRun(IdMixin, CreatedMixin, Base):
    __tablename__ = "audit_runs"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("uploads.id", ondelete="RESTRICT"))
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), default=RunStatus.queued)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)  # пороги правил на момент прогона
    mapping: Mapped[dict[str, Any] | None] = mapped_column()  # подтверждённое сопоставление колонок
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    lines_total: Mapped[int] = mapped_column(Integer, default=0)
    lines_ok: Mapped[int] = mapped_column(Integer, default=0)
    lines_needs_input: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    overpayment: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    market_points: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    report_key: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    upload: Mapped[Upload] = relationship(lazy="joined")

    @property
    def filename(self) -> str:
        return self.upload.filename if self.upload else ""


class PurchaseLine(IdMixin, Base):
    __tablename__ = "purchase_lines"
    __table_args__ = (
        Index("ix_purchase_lines_run_status", "run_id", "norm_status"),
        Index("ix_purchase_lines_org_sku", "org_id", "sku"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    line_key: Mapped[str] = mapped_column(String(32))  # id строки движка («r42»)
    source_row: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    doc_no: Mapped[str | None] = mapped_column(String(300))
    supplier: Mapped[str] = mapped_column(String(300))
    supplier_inn: Mapped[str | None] = mapped_column(String(32))
    item_raw: Mapped[str] = mapped_column(Text)
    qty: Mapped[Decimal]
    unit_raw: Mapped[str | None] = mapped_column(String(32))
    price: Mapped[Decimal]
    amount: Mapped[Decimal] = mapped_column(Money)
    vat_included: Mapped[bool | None] = mapped_column(Boolean)
    object_name: Mapped[str | None] = mapped_column(String(300))
    responsible: Mapped[str | None] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(200))
    sku_label: Mapped[str | None] = mapped_column(String(300))
    head: Mapped[str | None] = mapped_column(String(64))
    unit_base: Mapped[str | None] = mapped_column(String(16))
    qty_base: Mapped[Decimal | None]
    price_base: Mapped[Decimal | None]
    norm_status: Mapped[str] = mapped_column(String(16))
    norm_note: Mapped[str | None] = mapped_column(Text)
    overpayment: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    doc_in_no: Mapped[str | None] = mapped_column(String(64))  # входящий номер — счёт/накладная поставщика
    doc_in_date: Mapped[date | None] = mapped_column(Date)


class Finding(IdMixin, CreatedMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_run_money", "run_id", "overpayment"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(8))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(200))
    sku_label: Mapped[str | None] = mapped_column(String(300))
    supplier: Mapped[str | None] = mapped_column(String(300))
    responsible: Mapped[str | None] = mapped_column(String(200))
    reference: Mapped[Decimal | None]
    reference_kind: Mapped[str | None] = mapped_column(String(32))
    overpayment: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[FindingStatus] = mapped_column(_enum(FindingStatus), default=FindingStatus.open)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_reasons: Mapped[list[Any]] = mapped_column(default=list)  # быстрые причины: «Срочная поставка»…
    answer_review: Mapped[dict[str, Any] | None] = mapped_column()  # подсказка ИИ к ответу (prompts/answer_review)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    answered_at: Mapped[datetime | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column()

    lines: Mapped[list[FindingLine]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class FindingLine(Base):
    __tablename__ = "finding_lines"

    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), primary_key=True)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_lines.id", ondelete="CASCADE"), primary_key=True)
    overpayment: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))


class RunSupplier(IdMixin, Base):
    """Досье поставщика за прогон: обороты, через кого покупали, ИНН, реквизиты, следы связок."""

    __tablename__ = "run_suppliers"
    __table_args__ = (Index("ix_run_suppliers_run_spend", "run_id", "spend"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(300))
    inn: Mapped[str | None] = mapped_column(String(32))
    inn_kind: Mapped[str | None] = mapped_column(String(8))  # org | person — по номеру ИНН
    inn_date: Mapped[date | None] = mapped_column(Date)  # регистрация юрлица / рождение физлица
    spend: Mapped[Decimal] = mapped_column(Money)
    lines: Mapped[int] = mapped_column(Integer)
    share: Mapped[Decimal]
    first_date: Mapped[date] = mapped_column(Date)
    last_date: Mapped[date] = mapped_column(Date)
    skus: Mapped[int] = mapped_column(Integer, default=0)
    buyers: Mapped[list[Any]] = mapped_column(default=list)  # [{"name", "spend", "lines"}]
    card: Mapped[dict[str, Any] | None] = mapped_column()  # телефон, e-mail, адрес, счёт
    traces: Mapped[list[Any]] = mapped_column(default=list)  # все следы, в т.ч. не ставшие вопросом
    linked: Mapped[bool] = mapped_column(Boolean, default=False)  # есть находка LINK
    payment: Mapped[dict[str, Any] | None] = mapped_column()  # как платят: предоплата, задержка, аванс
    # сведения реестра, использованные в прогоне; «registry» у декларативной базы занято — атрибут с суффиксом
    registry_data: Mapped[dict[str, Any] | None] = mapped_column("registry")


class Payment(IdMixin, Base):
    """Оплата поставщику из выгрузки: списание с расчётного счёта или из кассы."""

    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_run_supplier", "run_id", "supplier"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"))
    payment_key: Mapped[str] = mapped_column(String(32))  # «p12» — номер записи в выгрузке
    date: Mapped[date] = mapped_column(Date)
    supplier: Mapped[str] = mapped_column(String(300))
    supplier_inn: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Money)
    doc_no: Mapped[str | None] = mapped_column(String(300))
    purpose: Mapped[str | None] = mapped_column(Text)


class RegistryRecordRow(IdMixin, CreatedMixin, Base):
    """Сведения реестра о поставщике по ИНН.

    org_id пусто — общий кэш агрегатора (публичные сведения, одни для всех клиентов);
    org_id задан — ручная проверка этой организации по выписке Минюста: видна только ей.
    """

    __tablename__ = "registry_records"
    __table_args__ = (
        UniqueConstraint("inn", "source", "org_id", name="uq_registry_records_inn", postgresql_nulls_not_distinct=True),
    )

    inn: Mapped[str] = mapped_column(String(32), index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))  # manual | statsnet | …
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)  # engine/registry.RegistryRecord.as_dict()
    checked_at: Mapped[datetime] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MarketPoint(IdMixin, CreatedMixin, Base):
    __tablename__ = "market_points"
    __table_args__ = (
        Index("ix_market_points_sku_date", "sku", "date"),
        UniqueConstraint(
            "source", "ref", "sku", "date", "price", name="uq_market_points_natural", postgresql_nulls_not_distinct=True
        ),
    )

    sku: Mapped[str] = mapped_column(String(200))
    date: Mapped[date] = mapped_column(Date)
    price: Mapped[Decimal]
    unit: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(200))
    ref: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(300))


class FxRate(Base):
    __tablename__ = "fx_rates"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate: Mapped[Decimal]  # сом за 1 единицу валюты (НБКР)
    source: Mapped[str] = mapped_column(String(32), default="nbkr")


class IntegrationToken(IdMixin, CreatedMixin, Base):
    """Токен интеграции организации: выгрузка из 1С по API, MCP-клиенты. Хранится только хэш."""

    __tablename__ = "integration_tokens"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))  # «sk_7Hq2…» — узнать токен в списке
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 от токена
    scopes: Mapped[list[Any]] = mapped_column(default=list)  # push, mcp
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()


class Connection(IdMixin, CreatedMixin, Base):
    """Подключение к учётной системе (сейчас — 1С по OData). Пароль — в secret, зашифрован SK_DATA_KEY."""

    __tablename__ = "connections"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # onec_odata
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)  # base_url, profile, username, period_days
    secret: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column()
    last_status: Mapped[str | None] = mapped_column(String(16))  # ok | error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audit_runs.id", ondelete="SET NULL"))
