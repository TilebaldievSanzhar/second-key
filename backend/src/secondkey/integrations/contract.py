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

    @model_validator(mode="after")
    def _price_or_amount(self) -> PurchaseIn:
        if self.price is None and self.amount is None:
            raise ValueError("нужна цена или сумма строки")
        return self


class PurchasesIn(BaseModel):
    source: str = Field(default="1c", max_length=64, description="Откуда строки: 1c:bp30, 1c:ut11, mcp, …")
    period_start: date | None = None
    period_end: date | None = None
    lines: list[PurchaseIn] = Field(min_length=1, max_length=200_000)

    def records(self) -> list[dict]:
        return [ln.model_dump(mode="json") for ln in self.lines]
