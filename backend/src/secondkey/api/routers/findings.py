"""Находки — вопросы по закупкам. Два ключа: снабжение отвечает, собственник решает."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.api.deps import ANSWER, MANAGE, CurrentMember, Member, Session, require
from secondkey.api.schemas import AnswerIn, DecisionIn, FindingDetail, FindingLineOut, FindingOut, Page, PersonRef
from secondkey.db.models import Finding, FindingStatus, PurchaseLine, Role, User

router = APIRouter(prefix="/findings", tags=["findings"])

DECIDED = (FindingStatus.confirmed, FindingStatus.justified, FindingStatus.data_error)


def _scope(stmt, member: Member):
    stmt = stmt.where(Finding.org_id == member.org_id)
    # снабженец видит только вопросы по своим закупкам; без привязки к имени в учёте — ничего
    if member.role == Role.buyer:
        stmt = stmt.where(Finding.responsible == member.alias if member.alias else false())
    return stmt


async def _finding(session: AsyncSession, finding_id: uuid.UUID, member: Member) -> Finding:
    f = await session.scalar(_scope(select(Finding).where(Finding.id == finding_id), member))
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вопрос не найден")
    return f


def _out(f: Finding) -> FindingOut:
    out = FindingOut.model_validate(f)
    out.lines_count = len(f.lines)
    return out


@router.get("", response_model=Page[FindingOut])
async def list_findings(
    session: Session,
    member: CurrentMember,
    run_id: uuid.UUID | None = None,
    finding_status: Annotated[list[FindingStatus] | None, Query(alias="status")] = None,
    code: Annotated[str | None, Query(pattern=r"^P-\d{2}$")] = None,
    responsible: Annotated[str | None, Query(max_length=200)] = None,
    supplier: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FindingOut]:
    stmt = _scope(select(Finding), member)
    if run_id:
        stmt = stmt.where(Finding.run_id == run_id)
    if finding_status:
        stmt = stmt.where(Finding.status.in_(finding_status))
    if code:
        stmt = stmt.where(Finding.code == code)
    if responsible:
        stmt = stmt.where(Finding.responsible == responsible)
    if supplier:
        stmt = stmt.where(Finding.supplier == supplier)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(stmt.order_by(Finding.overpayment.desc(), Finding.created_at).limit(limit).offset(offset))
    ).all()
    return Page[FindingOut](items=[_out(f) for f in rows], total=total or 0)


async def _person(session: AsyncSession, user_id: uuid.UUID | None) -> PersonRef | None:
    u = await session.get(User, user_id) if user_id else None
    return PersonRef(id=u.id, full_name=u.full_name) if u else None


@router.get("/{finding_id}", response_model=FindingDetail)
async def get_finding(finding_id: uuid.UUID, session: Session, member: CurrentMember) -> FindingDetail:
    f = await _finding(session, finding_id, member)
    share = {fl.line_id: fl.overpayment for fl in f.lines}
    rows = (
        await session.scalars(
            select(PurchaseLine).where(PurchaseLine.id.in_(share)).order_by(PurchaseLine.date, PurchaseLine.source_row)
        )
    ).all()
    lines = [FindingLineOut(**_line_dict(r), finding_overpayment=share[r.id]) for r in rows]
    return FindingDetail(
        **_out(f).model_dump(),
        evidence=f.evidence or {},
        lines=lines,
        answered_by=await _person(session, f.answered_by),
        decided_by=await _person(session, f.decided_by),
    )


def _line_dict(r: PurchaseLine) -> dict:
    return {k: getattr(r, k) for k in FindingLineOut.model_fields if k != "finding_overpayment"}


@router.post("/{finding_id}/answer", response_model=FindingOut)
async def answer(
    finding_id: uuid.UUID, body: AnswerIn, session: Session, member: Member = require(*ANSWER)
) -> FindingOut:
    """Ключ 1: снабжение объясняет отклонение."""
    f = await _finding(session, finding_id, member)
    if f.status in DECIDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Решение по вопросу уже принято — сначала верните его в работу")
    f.answer, f.answered_by, f.answered_at = body.answer.strip(), member.user.id, datetime.now(UTC)
    f.answer_reasons = list(dict.fromkeys(body.reasons))
    f.status = FindingStatus.answered
    await session.flush()
    return _out(f)


@router.post("/{finding_id}/decision", response_model=FindingOut)
async def decide(
    finding_id: uuid.UUID, body: DecisionIn, session: Session, member: Member = require(*MANAGE)
) -> FindingOut:
    """Ключ 2: собственник решает — переплата подтверждена, обосновано или ошибка учёта."""
    f = await _finding(session, finding_id, member)
    f.status, f.decision_note = body.status, (body.note or "").strip() or None
    f.decided_by, f.decided_at = member.user.id, datetime.now(UTC)
    await session.flush()
    return _out(f)


@router.post("/{finding_id}/reopen", response_model=FindingOut)
async def reopen(finding_id: uuid.UUID, session: Session, member: Member = require(*MANAGE)) -> FindingOut:
    """Отмена решения вместо подтверждений «вы уверены?»: вопрос возвращается в работу."""
    f = await _finding(session, finding_id, member)
    f.status = FindingStatus.answered if f.answer else FindingStatus.open
    f.decision_note, f.decided_by, f.decided_at = None, None, None
    await session.flush()
    return _out(f)
