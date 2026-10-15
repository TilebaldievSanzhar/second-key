"""Зависимости запросов: сессия БД, текущий пользователь и организация, проверка роли."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.config import settings
from secondkey.db.models import Membership, Organization, Role, User
from secondkey.db.session import sessionmaker
from secondkey.services.security import decode_token


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# scope="function": коммит до отправки ответа — клиент сразу видит свои изменения
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


def _token(request: Request) -> str | None:
    if token := request.cookies.get(settings.cookie_name):
        return token
    auth = request.headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else None


async def load_user(session: AsyncSession, request: Request) -> User:
    token = _token(request)
    user_id = decode_token(token) if token else None
    user = await session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите в систему")
    return user


async def current_user(request: Request, session: Session) -> User:
    return await load_user(session, request)


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass
class Member:
    user: User
    org: Organization
    role: Role
    alias: str | None

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id


def member_of(user: User, x_org_id: uuid.UUID | None) -> Member:
    """Организация запроса: заголовок X-Org-Id или единственная/первая, где состоит пользователь."""
    memberships: list[Membership] = sorted(user.memberships, key=lambda m: m.created_at)
    if x_org_id is not None:
        memberships = [m for m in memberships if m.org_id == x_org_id]
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к организации")
    m = memberships[0]
    return Member(user=user, org=m.org, role=m.role, alias=m.alias)


async def current_member(user: CurrentUser, x_org_id: Annotated[uuid.UUID | None, Header()] = None) -> Member:
    return member_of(user, x_org_id)


async def stream_member(request: Request, x_org_id: Annotated[uuid.UUID | None, Header()] = None) -> Member:
    """Для долгих потоков (SSE): своя короткая сессия, соединение с БД не держится весь поток."""
    async with sessionmaker()() as session:
        return member_of(await load_user(session, request), x_org_id)


CurrentMember = Annotated[Member, Depends(current_member)]

# кто что может
MANAGE = (Role.owner, Role.finance)  # решения по находкам, пороги
UPLOAD = (Role.owner, Role.finance, Role.accountant)  # загрузка выгрузок и запуск аудита
ANSWER = (Role.owner, Role.finance, Role.accountant, Role.buyer)
# сводка, все строки и отчёт по компании; снабжение видит только вопросы по своим закупкам
SEE_ALL = (Role.owner, Role.finance, Role.accountant, Role.viewer)


def require(*roles: Role):
    async def check(member: CurrentMember) -> Member:
        if member.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для этого действия")
        return member

    return Depends(check)
