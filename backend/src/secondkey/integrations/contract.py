"""Контракт строк закупок для выгрузки из 1С и других систем (API, MCP, OData).

Один формат для всех путей: обработка 1С шлёт его POST-запросом, MCP-инструмент принимает его же,
OData-коннектор собирает его из документов поступления. Дальше — те же проверки, что у таблицы.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class PurchaseIn(BaseModel):
    date: date
    doc: str | None = Field(default=None, max_length=300, description="Номер и дата документа, как в учёте")
    supplier: str = Field(min_length=1, max_length=300)
    supplier_inn: str | None = Field(default=None, max_length=32)
    item: str = Field(min_length=1, max_length=1000, description="Номенклатура как в учёте")
    unit: str | None = Field(default=None, max_length=32)
    qty: Decimal
    price: Decimal | None = None
    amount: Decimal | None = Field(default=None, description="Сумма строки с НДС")
    vat: Decimal | None = None
    object: str | None = Field(default=None, max_length=300, description="Объект, склад, подразделение")
    responsible: str | None = Field(default=None, max_length=200, description="Как ответственный записан в учёте")
    ref: str | None = Field(default=None, max_length=64, description="Ссылка документа в источнике (GUID 1С)")
    doc_in_no: str | None = Field(default=None, max_length=64, description="Входящий номер: счёт/накладная поставщика")
    doc_in_date: date | None = Field(default=None, description="Дата входящего документа")

    @model_validator(mode="after")
    def _price_or_amount(self) -> PurchaseIn:
        if self.price is None and self.amount is None:
            raise ValueError("нужна цена или сумма строки")
        return self


class SupplierIn(BaseModel):
    """Реквизиты поставщика из карточки контрагента: по ним видно «конкурентов» с одним владельцем."""

    name: str = Field(min_length=1, max_length=300, description="Как в строках закупок (supplier)")
    inn: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    bank_account: str | None = Field(default=None, max_length=64, description="Основной расчётный счёт")


class StaffIn(BaseModel):
    """Сотрудник: ИНН — только хэшем SHA-256 от «соль:ИНН» (соль — в ответе /integrations/ping)."""

    name: str = Field(min_length=1, max_length=200, description="Как сотрудник записан в учёте")
    inn_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str | None = Field(default=None, max_length=100, description="Должность: «прораб», «снабженец»")


class PaymentIn(BaseModel):
    """Оплата поставщику: списание с расчётного счёта или расходный кассовый ордер с видом «оплата поставщику»."""

    date: date
    supplier: str = Field(min_length=1, max_length=300, description="Как в строках закупок")
    supplier_inn: str | None = Field(default=None, max_length=32)
    amount: Decimal = Field(gt=0, description="Сумма с НДС")
    doc: str | None = Field(default=None, max_length=300, description="Номер и дата документа")
    purpose: str | None = Field(default=None, max_length=1000, description="Назначение платежа")


class PurchasesIn(BaseModel):
    source: str = Field(default="1c", max_length=64, description="Откуда строки: 1c:bp30, 1c:ut11, mcp, …")
    period_start: date | None = None
    period_end: date | None = None
    lines: list[PurchaseIn] = Field(min_length=1, max_length=200_000)
    suppliers: list[SupplierIn] = Field(default=[], max_length=50_000)
    staff: list[StaffIn] = Field(default=[], max_length=20_000)
    payments: list[PaymentIn] = Field(default=[], max_length=200_000)

    def records(self) -> list[dict]:
        return [ln.model_dump(mode="json") for ln in self.lines]

    def extra(self) -> dict:
        """Карточки поставщиков и сотрудники — рядом со строками в сохранённой выгрузке."""
        return {
            "suppliers": [x.model_dump(mode="json", exclude_none=True) for x in self.suppliers],
            "staff": [x.model_dump(mode="json", exclude_none=True) for x in self.staff],
            "payments": [x.model_dump(mode="json", exclude_none=True) for x in self.payments],
        }
