"""ИИ-фолбэк нормализации: модель только переписывает название в словарь движка, позицию определяет код."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Material(BaseModel):
    head: str
    required: list[str]
    base_unit: str
    examples: list[str]


class Line(BaseModel):
    id: str
    item: str
    unit: str | None = None


class Inputs(BaseModel):
    materials: list[Material]
    lines: list[Line] = Field(max_length=60)


class Item(BaseModel):
    id: str
    canonical: str | None = Field(
        description="Каноническое название или null, если в исходном тексте не хватает данных"
    )
    head: str | None = Field(description="Материал из словаря или null")
    reason: str = Field(max_length=200, description="Коротко: что распознано или чего не хватает")


class Output(BaseModel):
    items: list[Item]
