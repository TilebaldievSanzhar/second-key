"""Промпты по модулям: каждый модуль — пакет с prompt.toml, схемами входа/выхода и шаблонами Jinja2.

    prompts/
      _shared/macros.j2          общие правила: данные ≠ инструкции, язык, только JSON
      <module>/
        __init__.py              Inputs и Output (pydantic): что подаём и что принимаем
        prompt.toml              версия, описание, tier модели, температура
        system.j2, user.j2       шаблоны; переменные — только поля Inputs (StrictUndefined)

Модуль ничего не знает о провайдере: рендер здесь, вызов модели — secondkey.llm.run.
Версия из prompt.toml пишется в лог каждого вызова — по ней видно, какой промпт дал ответ.
"""

from __future__ import annotations

import importlib
import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class PromptSpec:
    module: str
    version: str
    description: str
    tier: Literal["light", "heavy"]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class PromptModule:
    spec: PromptSpec
    inputs: type[BaseModel]
    output: type[BaseModel]


@dataclass(frozen=True)
class Rendered:
    spec: PromptSpec
    system: str
    user: str


def _json(v: Any) -> str:
    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        return str(o)

    return json.dumps(v, ensure_ascii=False, default=default)


@cache
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        undefined=StrictUndefined,  # опечатка в переменной — ошибка, а не пустая строка в промпте
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,
    )
    env.filters["json"] = _json
    return env


def modules() -> list[str]:
    return sorted(p.name for p in PROMPTS_DIR.iterdir() if p.is_dir() and (p / "prompt.toml").exists())


@cache
def get(module: str) -> PromptModule:
    if module not in modules():
        raise KeyError(f"нет модуля промптов «{module}»")
    meta = tomllib.loads((PROMPTS_DIR / module / "prompt.toml").read_text(encoding="utf-8"))
    pkg = importlib.import_module(f"secondkey.prompts.{module}")
    spec = PromptSpec(
        module=module,
        version=str(meta["version"]),
        description=meta["description"],
        tier=meta.get("tier", "light"),
        temperature=float(meta.get("temperature", 0.0)),
        max_tokens=int(meta.get("max_tokens", 2048)),
    )
    return PromptModule(spec=spec, inputs=pkg.Inputs, output=pkg.Output)


def render(module: str, inputs: BaseModel, template: str | None = None) -> Rendered:
    """Рендер system/user по типизированному входу; template — отдельный шаблон модуля (напр. fallback.j2)."""
    m = get(module)
    if not isinstance(inputs, m.inputs):
        raise TypeError(f"{module}: ожидается {m.inputs.__name__}, получено {type(inputs).__name__}")
    ctx = inputs.model_dump(mode="python")
    env = _env()
    if template:
        text = env.get_template(f"{module}/{template}").render(**ctx).strip()
        return Rendered(spec=m.spec, system="", user=text)
    system = env.get_template(f"{module}/system.j2").render(**ctx).strip()
    user = env.get_template(f"{module}/user.j2").render(**ctx).strip()
    return Rendered(spec=m.spec, system=system, user=user)
