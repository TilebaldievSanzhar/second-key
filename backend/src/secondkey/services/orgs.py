"""Организации и пользователи."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import Membership, Organization, Role, User
from secondkey.services.security import hash_password


class Conflict(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))


async def create_org(session: AsyncSession, name: str, settings: dict | None = None) -> Organization:
    org = Organization(name=name, settings=settings or {})
    session.add(org)
    await session.flush()
    return org


async def add_member(
    session: AsyncSession,
    org: Organization,
    email: str,
    full_name: str,
    role: Role,
    password: str | None = None,
    alias: str | None = None,
) -> Membership:
    """Добавляет человека в организацию; новый пользователь создаётся с паролем."""
    user = await get_user_by_email(session, email)
    if user is None:
        if not password:
            raise Conflict("для нового пользователя нужен пароль")
        user = User(email=normalize_email(email), full_name=full_name, password_hash=hash_password(password))
        session.add(user)
        await session.flush()
    exists = await session.scalar(select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id))
    if exists:
        raise Conflict("пользователь уже состоит в организации")
    m = Membership(org_id=org.id, user_id=user.id, role=role, alias=alias)
    session.add(m)
    await session.flush()
    return m
