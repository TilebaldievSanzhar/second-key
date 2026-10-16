"""Реестры юрлиц через агрегаторы — адаптеры к общему виду engine/registry.RegistryRecord.

Машинного доступа к реестру Минюста КР нет (record.minjust.gov.kg — для людей, за защитой от ботов), поэтому
сведения — у агрегаторов по договору. Провайдер выбирается настройками: SK_REGISTRY_PROVIDER и ключ провайдера;
без ключа агрегатор выключен, работает ручная проверка по выписке в карточке поставщика.
"""

from __future__ import annotations

from typing import Protocol

from secondkey.config import settings
from secondkey.engine.registry import RegistryRecord


class RegistryError(Exception):
    """Ошибка провайдера с текстом для человека: ключ, лимит, недоступность."""


class RegistryProvider(Protocol):
    name: str

    async def lookup(self, inn: str) -> RegistryRecord | None: ...

    async def aclose(self) -> None: ...


def provider() -> RegistryProvider | None:
    if settings.registry_provider == "statsnet" and settings.statsnet_api_key.get_secret_value():
        from secondkey.registries.statsnet import StatsnetProvider

        return StatsnetProvider(settings.statsnet_api_key.get_secret_value(), settings.statsnet_base_url)
    return None


def provider_name() -> str | None:
    return settings.registry_provider if provider_configured() else None


def provider_configured() -> bool:
    return settings.registry_provider == "statsnet" and bool(settings.statsnet_api_key.get_secret_value())
