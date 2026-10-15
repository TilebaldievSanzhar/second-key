"""Проверка ответа снабжения: подсказка собственнику перед вторым ключом. Решение всегда за человеком."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Reason = Literal[
    "Срочная поставка",
    "Доставка включена в цену",
    "Другая марка или качество",
    "Отсрочка платежа",
    "Ошибка в учёте",
    "Другое",
]


class Inputs(BaseModel):
    question: str
    rule: str
    explanation: str
    overpayment: str
    supplier: str | None
    reference: str | None
    facts: list[str] = Field(description="Факты строк учёта, посчитанные кодом: даты, объёмы, цены")
    answer: str
    answer_reasons: list[str]


class Output(BaseModel):
    verdict: Literal["explains", "partial", "does_not_explain"]
    reasons_detected: list[Reason]
    checkable: list[str] = Field(max_length=3, description="Что можно проверить документами: накладная, договор, ТТН")
    followup: str | None = Field(default=None, max_length=240, description="Один уточняющий вопрос снабжению или null")
    summary: str = Field(max_length=240)
