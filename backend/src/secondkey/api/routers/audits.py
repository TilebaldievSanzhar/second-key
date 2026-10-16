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

from secondkey.api.deps import MANAGE, SEE_ALL, UPLOAD, CurrentMember, Member, Session, require, stream_member
from secondkey.api.schemas import (
    BreakdownRow,
    DigestOut,
    FindingCounts,
    LineOut,
    LinksOverview,
    Page,
    PaymentOut,
    PaymentProfileOut,
    PriceHistory,
    PricePoint,
    RegistryIn,
    RegistryOut,
    RunIn,
    RunOut,
    RunSummary,
    SupplierDetail,
    SupplierOut,
    TimelineMonth,
)
from secondkey.config import settings
from secondkey.db.models import (
    AuditRun,
    Finding,
    FindingLine,
    FindingStatus,
    MarketPoint,
    Organization,
    Payment,
    PurchaseLine,
    RunStatus,
    RunSupplier,
    Upload,
)
from secondkey.db.session import sessionmaker
from secondkey.engine.report import RULE_TITLES
from secondkey.services import links_view, progress, registry
from secondkey.services.audits import basic_report_key, create_run, remember_mapping
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
    # связка (LINK) денег не добавляет: её сумма — справочно, это переплата ценовых вопросов по тем же строкам
    confirmed = await session.scalar(
        select(func.coalesce(func.sum(Finding.overpayment), 0)).where(
            Finding.run_id == run_id, Finding.status == FindingStatus.confirmed, Finding.code != "LINK"
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


@router.get("/{run_id}/suppliers", response_model=list[SupplierOut])
async def suppliers(
    run_id: uuid.UUID,
    session: Session,
    member: Member = require(*MANAGE),
    linked: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[SupplierOut]:
    """Досье поставщиков прогона: обороты, через кого покупали, ИНН, реквизиты, следы связок (engine/links.py)."""
    await _run(session, run_id, member)
    stmt = select(RunSupplier).where(RunSupplier.run_id == run_id)
    if linked is not None:
        stmt = stmt.where(RunSupplier.linked == linked)
    rows = (await session.scalars(stmt.order_by(RunSupplier.spend.desc()).limit(limit))).all()
    return [SupplierOut.model_validate(r) for r in rows]


@router.get("/{run_id}/suppliers/{supplier_id}", response_model=SupplierDetail)
async def supplier(
    run_id: uuid.UUID, supplier_id: uuid.UUID, session: Session, member: Member = require(*MANAGE)
) -> SupplierDetail:
    """Досье поставщика: следы, реквизиты, через кого покупали, цены по позициям против других и рынка."""
    run = await _run(session, run_id, member)
    s = await session.get(RunSupplier, supplier_id)
    if s is None or s.run_id != run.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Поставщик не найден в этой проверке")
    return await _supplier_detail(session, run, s)


async def _supplier_detail(session: AsyncSession, run: AuditRun, s: RunSupplier) -> SupplierDetail:
    extra = await links_view.supplier_detail(session, run, s)
    rec = (await registry.records_for(session, run.org_id, {s.inn})).get(s.inn) if s.inn else None
    pays = (
        await session.scalars(
            select(Payment)
            .where(Payment.run_id == run.id, Payment.supplier == s.name)
            .order_by(Payment.date.desc())
            .limit(30)
        )
    ).all()
    return SupplierDetail(
        **SupplierOut.model_validate(s).model_dump(),
        **extra,
        registry=RegistryOut(**rec.as_dict()) if rec else None,
        payment=PaymentProfileOut(**s.payment) if s.payment else None,
        payments_list=[PaymentOut.model_validate(p) for p in pays],
    )


@router.put("/{run_id}/suppliers/{supplier_id}/registry", response_model=SupplierDetail)
async def save_registry(
    run_id: uuid.UUID, supplier_id: uuid.UUID, body: RegistryIn, session: Session, member: Member = require(*MANAGE)
) -> SupplierDetail:
    """Ручная проверка по выписке: сохраняется для организации по ИНН, связки проверки пересчитываются сразу —
    ответы снабжения и решения по уже заданным вопросам остаются."""
    from secondkey.services.links_refresh import refresh_links

    run = await _run(session, run_id, member)
    s = await session.get(RunSupplier, supplier_id)
    if s is None or s.run_id != run.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Поставщик не найден в этой проверке")
    if not s.inn:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "У поставщика нет ИНН в выгрузке — сверять не с чем")
    org_id, inn, name = run.org_id, s.inn, s.name
    await registry.save_manual(session, org_id, member.user.id, inn, {**body.model_dump(), "name": name})
    await session.commit()
    await refresh_links(run_id)  # своя сессия: следы и вопросы-связки пересчитаны
    session.expire_all()
    run = await session.get(AuditRun, run_id)
    s = await session.get(RunSupplier, supplier_id)
    return await _supplier_detail(session, run, s)


@router.post("/{run_id}/registry/fetch", status_code=status.HTTP_202_ACCEPTED)
async def fetch_registry(run_id: uuid.UUID, session: Session, member: Member = require(*MANAGE)) -> dict[str, str]:
    """Запросить сведения у агрегатора (Statsnet по ключу): сначала поставщики со следами, затем крупные."""
    from secondkey.registries import provider_configured
    from secondkey.worker.tasks import registry_fetch_task

    run = await _run(session, run_id, member)
    if not provider_configured():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Агрегатор реестров не подключён: нужен ключ SK_STATSNET_API_KEY. "
            "Пока — ручная проверка в карточке поставщика",
        )
    await registry_fetch_task.kiq(str(run.id))
    return {"status": "queued"}


@router.get("/{run_id}/links", response_model=LinksOverview)
async def links(run_id: uuid.UUID, session: Session, member: Member = require(*MANAGE)) -> LinksOverview:
    """Раздел «Связи»: матрица «снабженцы × поставщики» и вопросы-связки со следами (engine/links.py)."""
    run = await _run(session, run_id, member)
    return LinksOverview.model_validate(await links_view.links_overview(session, run))


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
                qty=r.qty_base,
                overpayment=r.overpayment,
                object=r.object_name,
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
    key = run.report_key
    if member.role not in MANAGE:
        # следы связок — собственнику и финансам; остальным — отчёт без них
        storage = get_storage()
        if await storage.exists(basic_report_key(run.report_key)):
            key = basic_report_key(run.report_key)
        elif await session.scalar(select(func.count()).where(Finding.run_id == run_id, Finding.code == "LINK")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Отчёт со связками доступен собственнику и финансам")
    html = await get_storage().get(key)
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
                Finding.code != "LINK",  # сумма связки повторяет ценовые вопросы
            )
            .order_by(Finding.overpayment.desc())
            .limit(3)
        )
    ).all()
    links_open = await session.scalar(
        select(func.count()).where(
            Finding.run_id == run_id,
            Finding.code == "LINK",
            Finding.status.in_([FindingStatus.open, FindingStatus.answered]),
        )
    )
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
        links=links_open or 0,
    )
    text, by = await owner_digest_text(inputs)
    return DigestOut(text=text, by=by)


@router.get("/{run_id}/timeline", response_model=list[TimelineMonth])
async def timeline(run_id: uuid.UUID, session: Session, member: Member = require(*SEE_ALL)) -> list[TimelineMonth]:
    """Помесячно: закупки, возможная переплата и её разбивка по правилам (строка — правилу с наибольшим вкладом)."""
    await _run(session, run_id, member)
    months: dict[str, dict] = {}
    rows = (
        await session.execute(
            select(PurchaseLine.id, PurchaseLine.date, PurchaseLine.amount, PurchaseLine.overpayment).where(
                PurchaseLine.run_id == run_id
            )
        )
    ).all()
    month_of: dict[uuid.UUID, str] = {}
    for lid, d, amount, over in rows:
        key = f"{d:%Y-%m}"
        month_of[lid] = key
        m = months.setdefault(
            key, {"spend": Decimal(0), "overpayment": Decimal(0), "lines": 0, "flagged": 0, "rules": {}}
        )
        m["spend"] += amount
        m["overpayment"] += over
        m["lines"] += 1
        m["flagged"] += int(over > 0)
    best: dict[uuid.UUID, tuple[Decimal, str]] = {}
    for code, line_id, v in (
        await session.execute(
            select(Finding.code, FindingLine.line_id, FindingLine.overpayment)
            .join(FindingLine, FindingLine.finding_id == Finding.id)
            .where(Finding.run_id == run_id)
        )
    ).all():
        if v > best.get(line_id, (Decimal(0), ""))[0]:
            best[line_id] = (v, code)
    for line_id, (v, code) in best.items():
        rules = months[month_of[line_id]]["rules"]
        rules[code] = rules.get(code, Decimal(0)) + v
    return [
        TimelineMonth(
            month=k,
            spend=m["spend"],
            overpayment=m["overpayment"],
            by_rule=m["rules"],
            lines=m["lines"],
            flagged_lines=m["flagged"],
        )
        for k, m in sorted(months.items())
    ]
