"""Тестовое окружение: SQLite-файл, локальное хранилище, брокер в памяти (задачи выполняются в kiq()).

Переменные окружения задаются до импорта secondkey — настройки читаются при импорте.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="secondkey-test-"))
os.environ.update(
    {
        "SK_ENV": "test",
        "SK_DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
        "SK_STORAGE_BACKEND": "local",
        "SK_STORAGE_LOCAL_DIR": str(_TMP / "storage"),
        "SK_JWT_SECRET": "test-secret-0123456789-0123456789",
        "SK_LOG_LEVEL": "WARNING",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402

from secondkey.db.base import Base  # noqa: E402
from secondkey.db.models import Role  # noqa: E402
from secondkey.db.session import dispose_engine, get_engine, session_scope  # noqa: E402

PASSWORD = "test-password-123"


@pytest.fixture(scope="session")
async def app():
    from secondkey.api.main import create_app
    from secondkey.worker.broker import broker

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    application = create_app()
    await broker.startup()
    yield application
    await broker.shutdown()
    await dispose_engine()


@pytest.fixture(scope="session")
async def org(app):
    """Организация с пользователями всех ролей; снабженец записан в учёте как «Асанов Б.»."""
    from secondkey.services.orgs import add_member, create_org

    async with session_scope() as s:
        o = await create_org(s, "ОсОО «Тест Курулуш»")
        for email, role, alias in [
            ("owner@t.kg", Role.owner, None),
            ("acc@t.kg", Role.accountant, None),
            ("buyer@t.kg", Role.buyer, "Асанов Б."),
            ("viewer@t.kg", Role.viewer, None),
        ]:
            await add_member(s, o, email, email.split("@")[0], role, PASSWORD, alias)
        return o


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, email: str) -> httpx.AsyncClient:
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client
