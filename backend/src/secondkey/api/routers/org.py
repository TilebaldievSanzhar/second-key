"""Организация: пороги правил аудита и участники."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from secondkey.api.deps import MANAGE, CurrentMember, Member, Session, require
from secondkey.api.schemas import MemberIn, MemberOut, NetworkIn, OrgOut, OrgSettings, OrgSettingsPatch, UserOut
from secondkey.db.models import Membership, Organization, Role
from secondkey.services.audits import run_params
from secondkey.services.orgs import Conflict, add_member

router = APIRouter(prefix="/org", tags=["org"])


def _org_out(org: Organization) -> OrgOut:
    return OrgOut(
        id=org.id,
        name=org.name,
        currency=org.currency,
        settings=OrgSettings(**run_params(org)),
        network_opt_in=bool((org.settings or {}).get("network_opt_in")),
    )


@router.get("", response_model=OrgOut)
async def get_org(member: CurrentMember) -> OrgOut:
    return _org_out(member.org)


@router.patch("/settings", response_model=OrgOut)
async def patch_settings(body: OrgSettingsPatch, session: Session, member: Member = require(*MANAGE)) -> OrgOut:
    org = await session.get(Organization, member.org_id)
    changes = {k: str(v) if not isinstance(v, int) else v for k, v in body.model_dump(exclude_none=True).items()}
    org.settings = {**(org.settings or {}), **changes}
    await session.flush()
    return _org_out(org)


@router.patch("/network", response_model=OrgOut)
async def patch_network(body: NetworkIn, session: Session, member: Member = require(*MANAGE)) -> OrgOut:
    """Обезличенный обмен ценами с другими клиентами: даёте свои — видите сеть (не меньше трёх компаний)."""
    org = await session.get(Organization, member.org_id)
    org.settings = {**(org.settings or {}), "network_opt_in": body.opt_in}
    await session.flush()
    return _org_out(org)


@router.get("/members", response_model=list[MemberOut])
async def members(session: Session, member: CurrentMember) -> list[MemberOut]:
    rows = (
        await session.scalars(
            select(Membership)
            .where(Membership.org_id == member.org_id)
            .options(selectinload(Membership.user))
            .order_by(Membership.created_at)
        )
    ).all()
    return [MemberOut(user=UserOut.model_validate(m.user), role=m.role, alias=m.alias) for m in rows]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def invite(body: MemberIn, session: Session, member: Member = require(Role.owner)) -> MemberOut:
    try:
        m = await add_member(session, member.org, body.email, body.full_name, body.role, body.password, body.alias)
    except Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.refresh(m, ["user"])
    return MemberOut(user=UserOut.model_validate(m.user), role=m.role, alias=m.alias)
