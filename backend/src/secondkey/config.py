"""Настройки сервиса. Всё — из переменных окружения с префиксом SK_ (см. .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SK_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+asyncpg://secondkey:secondkey@localhost:5432/secondkey"
    redis_url: str = "redis://localhost:6379/0"

    # хранилище файлов: s3 (MinIO/любой S3) или local (dev без MinIO, тесты)
    storage_backend: Literal["s3", "local"] = "s3"
    storage_local_dir: Path = Path("var/storage")
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "secondkey"
    s3_access_key: str = "secondkey"
    s3_secret_key: SecretStr = SecretStr("secondkey-secret")

    jwt_secret: SecretStr = SecretStr(DEFAULT_JWT_SECRET)
    jwt_ttl_minutes: int = 60 * 12
    cookie_name: str = "sk_session"
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]

    max_upload_mb: int = 25
    http_user_agent: str = "secondkey/0.1"
    market_cache_dir: Path = Path("var/cache/kg_goszakupki")

    # LLM: любой OpenAI-совместимый провайдер (OpenRouter по умолчанию, private-контур, vLLM).
    # Без ключа ИИ-функции выключены, у каждой есть детерминированная замена.
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "google/gemini-3.1-flash-lite-preview"  # tier light
    llm_model_heavy: str = "google/gemini-3.1-flash-lite-preview"  # tier heavy
    llm_model_overrides: dict[str, str] = {}  # {"answer_review": "<model>"}
    llm_structured: Literal["json_schema", "json_object"] = "json_schema"
    llm_timeout_s: float = 45
    llm_max_concurrent: int = 4
    llm_max_normalize_lines: int = 300  # потолок ИИ-нормализации на прогон

    # ключ шифрования учётных данных подключений (Fernet, 32 байта base64); без него OData-подключения не сохраняются
    data_key: SecretStr = SecretStr("")
    public_url: str = "http://localhost:5173"  # адрес для ссылок в дайджестах и инструкциях

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.get_secret_value()) and self.env != "test"

    @model_validator(mode="after")
    def _prod_requires_secrets(self) -> Settings:
        if self.env == "prod":
            if self.jwt_secret.get_secret_value() == DEFAULT_JWT_SECRET:
                raise ValueError("SK_JWT_SECRET обязателен в проде")
            if not self.cookie_secure:
                raise ValueError("в проде cookie должна быть Secure (SK_COOKIE_SECURE=true)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
