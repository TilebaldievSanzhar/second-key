"""Хранилище файлов клиентов: S3-совместимое (MinIO, AWS, Yandex…) или локальная папка (dev, тесты).

Ключи всегда начинаются с org/{org_id}/ — файлы разных организаций не пересекаются.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from secondkey.config import Settings, settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def ensure_ready(self) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("недопустимый ключ файла")
        return p

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(key)
        await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def ensure_ready(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)


class S3Storage:
    def __init__(self, cfg: Settings):
        import aioboto3

        self.cfg = cfg
        self.session = aioboto3.Session()

    def _client(self):
        return self.session.client(
            "s3",
            endpoint_url=self.cfg.s3_endpoint_url,
            region_name=self.cfg.s3_region,
            aws_access_key_id=self.cfg.s3_access_key,
            aws_secret_access_key=self.cfg.s3_secret_key.get_secret_value(),
        )

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        async with self._client() as s3:
            extra = {"ContentType": content_type} if content_type else {}
            await s3.put_object(Bucket=self.cfg.s3_bucket, Key=key, Body=data, **extra)

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            obj = await s3.get_object(Bucket=self.cfg.s3_bucket, Key=key)
            async with obj["Body"] as body:
                return await body.read()

    async def ensure_ready(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.cfg.s3_bucket)
            except Exception:
                await s3.create_bucket(Bucket=self.cfg.s3_bucket)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage(settings) if settings.storage_backend == "s3" else LocalStorage(settings.storage_local_dir)
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Подмена в тестах."""
    global _storage
    _storage = storage
