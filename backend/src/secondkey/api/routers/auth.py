"""Вход по email и паролю: сессия — JWT в httpOnly-cookie (для скриптов допустим Bearer)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from secondkey.api.deps import CurrentUser, Session
from secondkey.api.schemas import LoginIn, MembershipOut, MeOut, UserOut
from secondkey.config import settings
from secondkey.db.models import User
from secondkey.observability import log
from secondkey.services.orgs import get_user_by_email
from secondkey.services.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# сравнение с фиктивным хэшем, когда пользователя нет: время ответа не выдаёт, есть ли такой email
_DUMMY_HASH = hash_password("timing-equalizer")


def _me(user: User) -> MeOut:
    ms = sorted(user.memberships, key=lambda m: m.created_at)
    return MeOut(user=UserOut.model_validate(user), memberships=[MembershipOut.model_validate(m) for m in ms])


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, response: Response, session: Session) -> MeOut:
    user = await get_user_by_email(session, body.email)
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not (user and ok and user.is_active):
        log.info("auth.login_failed", email_domain=body.email.split("@")[-1])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    response.set_cookie(
        settings.cookie_name,
        create_token(user.id),
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return _me(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser) -> MeOut:
    return _me(user)
