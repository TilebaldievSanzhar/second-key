"""Дайджест собственнику: модель только формулирует, числа считает код; есть шаблон без модели (fallback.j2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Top(BaseModel):
    question: str
    amount: str


class Inputs(BaseModel):
    company: str
    period: str
    spend: str
    overpayment: str
    share: str
    confirmed: str
    waiting: int
    answered: int
    open: int
    top: list[Top] = Field(max_length=3)
    link: str


class Output(BaseModel):
    text: str = Field(max_length=900)
