"""Шифрование учётных данных подключений (Fernet, ключ SK_DATA_KEY). Без ключа пароли не сохраняются."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from secondkey.config import settings


class SecretsUnavailable(Exception):
    pass


def _fernet() -> Fernet:
    key = settings.data_key.get_secret_value()
    if not key:
        raise SecretsUnavailable("не задан SK_DATA_KEY — сохранять пароли подключений нельзя")
    return Fernet(key.encode())


def seal(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def unseal(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise SecretsUnavailable("учётные данные подключения не расшифровываются — сменился SK_DATA_KEY?") from exc
