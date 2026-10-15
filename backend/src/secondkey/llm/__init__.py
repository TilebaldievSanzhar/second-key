"""Вызов модели по модулю промптов: рендер → модель → JSON → pydantic-валидация.

    from secondkey.llm import run
    out = await run("answer_review", Inputs(...))

Провайдер — любой OpenAI-совместимый (OpenRouter, private-контур, vLLM): меняется настройками SK_LLM_*.
Без ключа run() бросает LLMDisabled, а сценарии используют детерминированную замену.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from secondkey.config import settings
from secondkey.observability import log
from secondkey.prompts import get, render

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Модель не дала валидный ответ."""


class LLMDisabled(LLMError):
    """ИИ выключен: нет ключа или тестовое окружение."""


class Client(Protocol):
    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        name: str,
        temperature: float,
        max_tokens: int,
    ) -> str: ...


# --- JSON-схема для провайдеров: без $ref и ключей, которые строгий режим отвергает ---------------
_DROP = frozenset(
    {
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
        "title",
    }
)


def prepare_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k not in _DROP and k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    text = text.strip()
    if m := _FENCE.search(text):
        return m.group(1).strip()
    a, b = text.find("{"), text.rfind("}")
    return text[a : b + 1] if a != -1 and b > a else text


class OpenAICompatClient:
    """Строгая JSON-схема (json_schema), откат на json_object со схемой в промпте для моделей без strict."""

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self._api = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_s,
            max_retries=2,
        )
        self._sem = asyncio.Semaphore(settings.llm_max_concurrent)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        name: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        from openai import BadRequestError

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        async with self._sem:
            if settings.llm_structured == "json_schema":
                try:
                    r = await self._api.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format={
                            "type": "json_schema",
                            "json_schema": {"name": name, "schema": schema, "strict": False},
                        },
                    )
                    return r.choices[0].message.content or ""
                except BadRequestError as exc:  # модель не умеет json_schema — пробуем json_object
                    log.info("llm.json_schema_unsupported", model=model, error=str(exc)[:200])
            messages[0]["content"] = (
                f"{system}\n\nВерни JSON-объект по этой JSON Schema (плоский, без обёртки properties):\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            )
            r = await self._api.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return r.choices[0].message.content or ""


_client: Client | None = None


def set_client(client: Client | None) -> None:
    """Подмена в тестах: фейковый клиент с заготовленными ответами."""
    global _client
    _client = client


def get_client() -> Client:
    global _client
    if _client is None:
        if not settings.llm_enabled:
            raise LLMDisabled("ИИ выключен: задайте SK_LLM_API_KEY")
        _client = OpenAICompatClient()
    return _client


def enabled() -> bool:
    return _client is not None or settings.llm_enabled


def resolve_model(module: str, tier: str) -> str:
    return settings.llm_model_overrides.get(module) or (
        settings.llm_model_heavy if tier == "heavy" else settings.llm_model
    )


async def run(module: str, inputs: BaseModel, *, attempts: int = 2) -> Any:
    """Вызов модуля промптов; ответ валидируется схемой Output модуля. Неудача — LLMError."""
    pm = get(module)
    rendered = render(module, inputs)
    model = resolve_model(module, pm.spec.tier)
    schema = prepare_schema(pm.output.model_json_schema())
    client = get_client()
    last: Exception | None = None
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            raw = await client.complete(
                model=model,
                system=rendered.system,
                user=rendered.user,
                schema=schema,
                name=module,
                temperature=pm.spec.temperature if attempt == 0 else max(pm.spec.temperature, 0.3),
                max_tokens=pm.spec.max_tokens,
            )
            out = pm.output.model_validate_json(extract_json(raw))
            log.info(
                "llm.call",
                module=module,
                version=pm.spec.version,
                model=model,
                attempt=attempt + 1,
                ok=True,
                ms=round((time.perf_counter() - started) * 1000),
            )
            return out
        except (ValidationError, ValueError) as exc:
            last = exc
            log.warning(
                "llm.invalid_output",
                module=module,
                version=pm.spec.version,
                model=model,
                attempt=attempt + 1,
                error=str(exc)[:300],
            )
        except LLMError:
            raise
        except Exception as exc:  # сеть, лимиты, 5xx провайдера
            last = exc
            log.warning("llm.call_failed", module=module, model=model, attempt=attempt + 1, error=str(exc)[:300])
    raise LLMError(f"{module}: модель не дала валидный ответ ({last})")
