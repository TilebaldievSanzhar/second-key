"""Прогоны аудита: запуск, прогресс (SSE), сводка, строки, история цен, отчёт."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.api.deps import SEE_ALL, UPLOAD, CurrentMember, Member, Session, require, stream_member
from secondkey.api.schemas import (
    BreakdownRow,
    DigestOut,
    FindingCounts,
    LineOut,
    Page,
    PriceHistory,
    PricePoint,
    RunIn,
    RunOut,
    RunSummary,
)
from secondkey.config import settings
from secondkey.db.models import (
    AuditRun,
    Finding,
    FindingLine,
    FindingStatus,
    MarketPoint,
    Organization,
    PurchaseLine,
    RunStatus,
    Upload,
)
from secondkey.db.session import sessionmaker
from secondkey.engine.report import RULE_TITLES
from secondkey.services import progress
from secondkey.services.audits import create_run, remember_mapping
from secondkey.services.storage import get_storage
from secondkey.worker.tasks import run_audit_task

router = APIRouter(prefix="/audits", tags=["audits"])

TERMINAL = {RunStatus.done.value, RunStatus.failed.value}


async def _run(session: AsyncSession, run_id: uuid.UUID, member: Member) -> AuditRun:
    run = await session.get(AuditRun, run_id)
    if run is None or run.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аудит не найден")
    return run


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def start(body: RunIn, session: Session, member: Member = require(*UPLOAD)) -> AuditRun:
    upload = await session.get(Upload, body.upload_id)
    if upload is None or upload.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    if body.mapping is None and upload.preview.get("missing"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Не все обязательные колонки распознаны — укажите их вручную"
        )
    org = await session.get(Organization, member.org_id)
    header_row = body.header_row or upload.preview.get("header_row") or None
    if body.mapping:
        await remember_mapping(session, org, upload, body.mapping, header_row)
    run = await create_run(session, org, upload, member.user.id, body.mapping, header_row)
    await session.commit()  # воркер должен увидеть прогон до того, как возьмёт задачу
    await run_audit_task.kiq(str(run.id))
    await session.refresh(run)
    return run


@router.get("", response_model=Page[RunOut])
async def list_runs(
    session: Session,
    member: CurrentMember,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    q = select(AuditRun).where(AuditRun.org_id == member.org_id)
    total = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await session.scalars(q.order_by(AuditRun.created_at.desc()).limit(limit).offset(offset))).all()
    return Page[RunOut](items=[RunOut.model_validate(r) for r in rows], total=total or 0)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: Session, member: CurrentMember) -> AuditRun:
    return await _run(session, run_id, member)


@router.post("/{run_id}/retry", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def retry(run_id: uuid.UUID, session: Session, member: Member = require(*UPLOAD)) -> AuditRun:
    run = await _run(session, run_id, member)
    if run.status != RunStatus.failed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Повторить можно только аудит, который завершился ошибкой")
    run.status, run.progress, run.error = RunStatus.queued, 0, None
    await session.commit()
    await run_audit_task.kiq(str(run.id))
    return run


async def _snapshot(run_id: uuid.UUID, member: Annotated[Member, Depends(stream_member)]) -> dict:
    async with sessionmaker()() as s:
        run = await _run(s, run_id, member)
        return {"status": run.status.value, "progress": run.progress, "error": run.error}


@router.get("/{run_id}/events", response_class=EventSourceResponse)
async def events(
    run_id: uuid.UUID, request: Request, snapshot: Annotated[dict, Depends(_snapshot)]
) -> AsyncIterator[ServerSentEvent]:
    """Прогресс прогона. Первое событие — текущее состояние из БД, дальше — изменения до done/failed."""
    yield ServerSentEvent(data=snapshot, event="progress", retry=3000)
    if snapshot["status"] in TERMINAL or settings.env == "test":
        return
    redis = await progress.get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(progress.channel(run_id))
    try:
        # событие могло прийти между снимком и подпиской — перечитываем состояние
        async with sessionmaker()() as s:
            run = await s.get(AuditRun, run_id)
            if run.status.value != snapshot["status"]:
                snapshot = {"status": run.status.value, "progress": run.progress, "error": run.error}
                yield ServerSentEvent(data=snapshot, event="progress")
                if snapshot["status"] in TERMINAL:
                    return
        while not await request.is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
            if msg is None:
                continue
            data = json.loads(msg["data"])
            yield ServerSentEvent(data=data, event="progress")
            if data.get("status") in TERMINAL:
                return
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


def _rows(result) -> list[BreakdownRow]:
    return [
        BreakdownRow(key=k or "—", label=k or "не указано", spend=sp or 0, overpayment=ov or 0, lines=n or 0)
        for k, sp, ov, n in result
    ]


async def _breakdown(session: AsyncSession, run_id: uuid.UUID, col, limit: int = 12) -> list[BreakdownRow]:
    over = func.sum(PurchaseLine.overpayment)
    q = (
        select(col, func.sum(PurchaseLine.amount), over, func.sum(case((PurchaseLine.overpayment > 0, 1), else_=0)))
        .where(PurchaseLine.run_id == run_id)
        .group_by(col)
        .having(over > 0)
        .order_by(over.desc())
        .limit(limit)
    )
    return _rows((await session.execute(q)).all())


@router.get("/{run_id}/summary", response_model=RunSummary)
async def summary(run_id: uuid.UUID, session: Session, member: Member = require(*SEE_ALL)) -> RunSummary:
    run = await _run(session, run_id, member)

    counts = FindingCounts()
    for st, n in (
        await session.execute(
            select(Finding.status, func.count()).where(Finding.run_id == run_id).group_by(Finding.status)
        )
    ).all():
        setattr(counts, FindingStatus(st).value, n)
    confirmed = await session.scalar(
        select(func.coalesce(func.sum(Finding.overpayment), 0)).where(
            Finding.run_id == run_id, Finding.status == FindingStatus.confirmed
        )
    )

    # деньги по правилам без двойного счёта: строка относится к правилу с наибольшим вкладом
    best: dict[uuid.UUID, tuple[Decimal, str]] = {}
    count_by_code: dict[str, int] = defaultdict(int)
    for code, line_id, v in (
        await session.execute(
            select(Finding.code, FindingLine.line_id, FindingLine.overpayment)
            .join(FindingLine, FindingLine.finding_id == Finding.id)
            .where(Finding.run_id == run_id)
        )
    ).all():
        if v > best.get(line_id, (Decimal("0"), ""))[0]:
            best[line_id] = (v, code)
    for code, n in (
        await session.execute(select(Finding.code, func.count()).where(Finding.run_id == run_id).group_by(Finding.code))
    ).all():
        count_by_code[code] = n
    money_by_code: dict[str, Decimal] = defaultdict(Decimal)
    lines_by_code: dict[str, int] = defaultdict(int)
    for v, code in best.values():
        money_by_code[code] += v
        lines_by_code[code] += 1
    by_rule = [
        BreakdownRow(
            key=c,
            label=RULE_TITLES.get(c, c),
            spend=Decimal("0"),
            overpayment=money_by_code[c],
            lines=lines_by_code[c],
            findings=count_by_code[c],
        )
        for c in sorted(count_by_code)
    ]

    return RunSummary(
        run=RunOut.model_validate(run),
        findings=counts,
        confirmed_overpayment=confirmed or 0,
        by_rule=by_rule,
        by_supplier=await _breakdown(session, run_id, PurchaseLine.supplier),
        by_responsible=await _breakdown(session, run_id, PurchaseLine.responsible),
        by_material=await _breakdown(session, run_id, PurchaseLine.head),
    )


@router.get("/{run_id}/lines", response_model=Page[LineOut])
async def lines(
    run_id: uuid.UUID,
    session: Session,
    member: Member = require(*SEE_ALL),
    norm_status: Annotated[str | None, Query(alias="status", pattern="^(ok|needs_input|unit_error)$")] = None,
    flagged: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[LineOut]:
    await _run(session, run_id, member)
    stmt = select(PurchaseLine).where(PurchaseLine.run_id == run_id)
    if norm_status:
        stmt = stmt.where(PurchaseLine.norm_status == norm_status)
    if flagged is not None:
        stmt = stmt.where(PurchaseLine.overpayment > 0 if flagged else PurchaseLine.overpayment == 0)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(PurchaseLine.item_raw).like(like) | func.lower(PurchaseLine.supplier).like(like))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(stmt.order_by(PurchaseLine.date, PurchaseLine.source_row).limit(limit).offset(offset))
    ).all()
    return Page[LineOut](items=[LineOut.model_validate(r) for r in rows], total=total or 0)


@router.get("/{run_id}/price-history", response_model=PriceHistory)
async def price_history(
    run_id: uuid.UUID, sku: Annotated[str, Query(min_length=1, max_length=200)], session: Session, member: CurrentMember
) -> PriceHistory:
    """Покупки позиции в прогоне и рыночные точки за тот же период — для графика в карточке находки."""
    run = await _run(session, run_id, member)
    rows = (
        await session.scalars(
            select(PurchaseLine)
            .where(PurchaseLine.run_id == run_id, PurchaseLine.sku == sku)
            .order_by(PurchaseLine.date)
        )
    ).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Позиция не найдена в этом аудите")
    lo = (run.period_start or rows[0].date) - timedelta(days=30)
    hi = (run.period_end or rows[-1].date) + timedelta(days=30)
    market = (
        await session.scalars(
            select(MarketPoint)
            .where(MarketPoint.sku == sku, MarketPoint.date.between(lo, hi))
            .order_by(MarketPoint.date)
        )
    ).all()
    return PriceHistory(
        sku=sku,
        label=rows[0].sku_label,
        unit=rows[0].unit_base,
        purchases=[
            PricePoint(
                date=r.date,
                price=r.price_base,
                supplier=r.supplier,
                source="purchase",
                ref=r.doc_no,
                flagged=r.overpayment > 0,
                line_id=r.id,
            )
            for r in rows
            if r.price_base is not None
        ],
        market=[PricePoint(date=m.date, price=m.price, source=m.source, ref=m.ref) for m in market],
    )


@router.get("/{run_id}/report", response_class=HTMLResponse)
async def report(
    run_id: uuid.UUID, session: Session, member: Member = require(*SEE_ALL), download: bool = False
) -> HTMLResponse:
    run = await _run(session, run_id, member)
    if not run.report_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчёт ещё не готов")
    html = await get_storage().get(run.report_key)
    headers = {"Content-Disposition": f'attachment; filename="second-key-audit-{run_id}.html"'} if download else {}
    return HTMLResponse(html.decode("utf-8"), headers=headers)


def _short(v: Decimal) -> str:
    v = Decimal(v)
    if v >= 1_000_000:
        n = v / 1_000_000
        return f"{n:.0f} млн сом" if n >= 100 else f"{n:.1f} млн сом".replace(".", ",")
    return f"{v:,.0f} сом".replace(",", " ")


@router.get("/{run_id}/digest", response_model=DigestOut)
async def digest(run_id: uuid.UUID, session: Session, member: Member = require(*SEE_ALL)) -> DigestOut:
    """Текст собственнику для WhatsApp: prompts/owner_digest, при сбое или без ИИ — шаблон."""
    from secondkey.prompts import owner_digest
    from secondkey.services.ai import owner_digest_text

    run = await _run(session, run_id, member)
    org = await session.get(Organization, member.org_id)
    counts = dict(
        (
            await session.execute(
                select(Finding.status, func.count()).where(Finding.run_id == run_id).group_by(Finding.status)
            )
        ).all()
    )
    confirmed = await session.scalar(
        select(func.coalesce(func.sum(Finding.overpayment), 0)).where(
            Finding.run_id == run_id, Finding.status == FindingStatus.confirmed
        )
    )
    top = (
        await session.scalars(
            select(Finding)
            .where(
                Finding.run_id == run_id,
                Finding.status.in_([FindingStatus.open, FindingStatus.answered]),
                Finding.overpayment > 0,
            )
            .order_by(Finding.overpayment.desc())
            .limit(3)
        )
    ).all()
    share = (run.overpayment / run.spend * 100) if run.spend else Decimal(0)
    inputs = owner_digest.Inputs(
        company=org.name,
        period=f"{run.period_start:%d.%m.%Y} — {run.period_end:%d.%m.%Y}" if run.period_start else "период проверки",
        spend=_short(run.spend),
        overpayment=_short(run.overpayment),
        share=f"{share:.1f}%".replace(".", ","),
        confirmed=_short(confirmed or 0),
        waiting=counts.get(FindingStatus.open, 0) + counts.get(FindingStatus.answered, 0),
        answered=counts.get(FindingStatus.answered, 0),
        open=counts.get(FindingStatus.open, 0),
        top=[
            owner_digest.Top(
                question=f"{f.sku_label or f.title} · {f.supplier or 'несколько поставщиков'} — "
                f"{RULE_TITLES.get(f.code, f.title).lower()}",
                amount=_short(f.overpayment),
            )
            for f in top
        ],
        link=f"{settings.public_url.rstrip('/')}/",
    )
    text, by = await owner_digest_text(inputs)
    return DigestOut(text=text, by=by)
