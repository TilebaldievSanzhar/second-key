"""Сопоставление колонок: модель предлагает, код проверяет индексы, уникальность и обязательные поля."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldSpec(BaseModel):
    key: str
    label: str
    required: bool
    hint: str


class Inputs(BaseModel):
    fields: list[FieldSpec]
    rows: list[list[str]] = Field(description="Первые строки файла как есть (до 8)")


class Output(BaseModel):
    header_row: int = Field(description="Номер строки с заголовками среди переданных, с 1; 0 — заголовков нет")
    mapping: dict[str, int] = Field(description="Поле → индекс колонки с 0")
    notes: str = Field(default="", max_length=300)
