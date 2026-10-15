"""Токены интеграций: показываются один раз, в базе — только sha256. Префикс помогает узнать токен в списке."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import IntegrationToken

SCOPES = ("push", "mcp")
PREFIX = "sk_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(
    session: AsyncSession, org_id: uuid.UUID, name: str, scopes: list[str], user_id: uuid.UUID | None
) -> tuple[IntegrationToken, str]:
    token = PREFIX + secrets.token_urlsafe(32)
    row = IntegrationToken(
        org_id=org_id,
        name=name,
        prefix=token[:10],
        token_hash=_hash(token),
        scopes=[s for s in scopes if s in SCOPES],
        created_by=user_id,
    )
    session.add(row)
    await session.flush()
    return row, token


async def verify(session: AsyncSession, token: str, scope: str) -> IntegrationToken | None:
    if not token.startswith(PREFIX):
        return None
    row = await session.scalar(select(IntegrationToken).where(IntegrationToken.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None or scope not in (row.scopes or []):
        return None
    row.last_used_at = datetime.now(UTC)
    return row
