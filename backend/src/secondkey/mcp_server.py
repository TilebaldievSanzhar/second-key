"""MCP-сервер «Второго ключа»: проверки, вопросы и цены — для ИИ-агентов (Claude, Cursor, локальные модели).

Адрес: {сервис}/api/mcp/ (Streamable HTTP, без состояния). Доступ — токен интеграции организации
с правом «mcp» в заголовке Authorization: Bearer sk_…; отправка строк закупок — ещё и право «push».

Связка с 1С: агент читает документы поступления через MCP-сервер 1С (1c-odata-mcp, mcp-1c, RSV Data)
и отдаёт строки сюда инструментом submit_purchases — рецепт в промпте export_purchases_from_1c.
"""

from __future__ import annotations

import contextvars
import uuid
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from sqlalchemy import func, select
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from secondkey.db.models import AuditRun, Finding, FindingStatus, MarketPoint, PurchaseLine, RunStatus
from secondkey.db.session import session_scope
from secondkey.engine.report import RULE_TITLES
from secondkey.integrations import tokens
from secondkey.integrations.contract import PurchasesIn

_token: contextvars.ContextVar[tuple[uuid.UUID, tuple[str, ...]] | None] = contextvars.ContextVar(
    "sk_mcp_token", default=None
)
WAITING = (FindingStatus.open, FindingStatus.answered)
DECIDED = (FindingStatus.confirmed, FindingStatus.justified, FindingStatus.data_error)

INSTRUCTIONS = """«Второй ключ» проверяет закупки стройматериалов строительной компании по данным 1С.
Проверка (audit) — один файл или выгрузка за период; вопрос (question) — найденное отклонение цены:
дороже другого поставщика в те же недели, дороже рынка, документ учтён дважды, дробление закупки.
Суммы — строки в сомах; «возможная переплата» до решения собственника, «подтверждённая» после.
Не называй отклонения воровством: это вопросы, на которые снабжение отвечает, а собственник решает."""


class TokenAuth:
    """ASGI-обёртка: токен организации → контекст запроса; без токена — 401 с понятным текстом."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        async with session_scope() as s:
            row = await tokens.verify(s, raw, "mcp") if raw else None
            ctx = (row.org_id, tuple(row.scopes or [])) if row else None
        if ctx is None:
            await JSONResponse({"detail": "Нужен токен интеграции с правом «mcp» (Bearer sk_…)"}, status_code=401)(
                scope, receive, send
            )
            return
        reset = _token.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _token.reset(reset)


def _org() -> uuid.UUID:
    ctx = _token.get()
    if ctx is None:
        raise PermissionError("нет токена организации")
    return ctx[0]


def _s(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v.isoformat() if hasattr(v, "isoformat") else v


def _run_dict(r: AuditRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "status": r.status.value,
        "file": r.filename,
        "period": [_s(r.period_start), _s(r.period_end)],
        "lines": r.lines_total,
        "spend": _s(r.spend),
        "overpayment": _s(r.overpayment),
        "questions": r.findings_count,
        "created_at": _s(r.created_at),
        "error": r.error,
    }


def _question(f: Finding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "rule": RULE_TITLES.get(f.code, f.title),
        "code": f.code,
        "position": f.sku_label,
        "sku": f.sku,
        "supplier": f.supplier,
        "responsible": f.responsible,
        "overpayment": _s(f.overpayment),
        "status": f.status.value,
        "explanation": f.message,
        "answer": f.answer,
    }


async def _resolve_run(s, run_id: str | None) -> AuditRun:
    org = _org()
    if run_id:
        run = await s.get(AuditRun, uuid.UUID(run_id))
        if run is None or run.org_id != org:
            raise ValueError("проверка не найдена")
        return run
    run = await s.scalar(
        select(AuditRun)
        .where(AuditRun.org_id == org, AuditRun.status == RunStatus.done)
        .order_by(AuditRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        raise ValueError("завершённых проверок пока нет — загрузите выгрузку из 1С")
    return run


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "second-key",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # хост проверяет прокси, доступ — токен организации; защита для localhost-серверов здесь мешала бы
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def list_audits(limit: int = 10) -> list[dict[str, Any]]:
        """Последние проверки закупок организации: период, строки, закупки и возможная переплата."""
        async with session_scope() as s:
            rows = (
                await s.scalars(
                    select(AuditRun)
                    .where(AuditRun.org_id == _org())
                    .order_by(AuditRun.created_at.desc())
                    .limit(max(1, min(limit, 50)))
                )
            ).all()
            return [_run_dict(r) for r in rows]

    @mcp.tool()
    async def audit_summary(audit_id: str | None = None) -> dict[str, Any]:
        """Сводка проверки (по умолчанию — последней): переплата, что ждёт решения, крупнейшие вопросы, разрезы."""
        async with session_scope() as s:
            run = await _resolve_run(s, audit_id)
            counts = {
                st.value: n
                for st, n in (
                    await s.execute(
                        select(Finding.status, func.count()).where(Finding.run_id == run.id).group_by(Finding.status)
                    )
                ).all()
            }
            top = (
                await s.scalars(
                    select(Finding)
                    .where(Finding.run_id == run.id, Finding.status.in_(WAITING))
                    .order_by(Finding.overpayment.desc())
                    .limit(10)
                )
            ).all()
            cuts = {}
            for name, col in (
                ("by_supplier", PurchaseLine.supplier),
                ("by_responsible", PurchaseLine.responsible),
                ("by_material", PurchaseLine.head),
            ):
                over = func.sum(PurchaseLine.overpayment)
                rows = (
                    await s.execute(
                        select(col, over)
                        .where(PurchaseLine.run_id == run.id)
                        .group_by(col)
                        .having(over > 0)
                        .order_by(over.desc())
                        .limit(5)
                    )
                ).all()
                cuts[name] = [{"name": k or "не указано", "overpayment": _s(v)} for k, v in rows]
            return {
                "audit": _run_dict(run),
                "questions_by_status": counts,
                "top_waiting": [_question(f) for f in top],
                **cuts,
            }

    @mcp.tool()
    async def list_questions(
        audit_id: str | None = None,
        status: str = "waiting",
        code: str | None = None,
        supplier: str | None = None,
        responsible: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Вопросы проверки, крупные сверху. status: waiting | decided | all; code: P-01…P-07."""
        async with session_scope() as s:
            run = await _resolve_run(s, audit_id)
            q = select(Finding).where(Finding.run_id == run.id)
            if status == "waiting":
                q = q.where(Finding.status.in_(WAITING))
            elif status == "decided":
                q = q.where(Finding.status.in_(DECIDED))
            if code:
                q = q.where(Finding.code == code)
            if supplier:
                q = q.where(Finding.supplier == supplier)
            if responsible:
                q = q.where(Finding.responsible == responsible)
            rows = (await s.scalars(q.order_by(Finding.overpayment.desc()).limit(max(1, min(limit, 100))))).all()
            return [_question(f) for f in rows]

    @mcp.tool()
    async def get_question(question_id: str) -> dict[str, Any]:
        """Вопрос со строками учёта: дата, документ, поставщик, номенклатура как в 1С, цена, переплата по строке."""
        async with session_scope() as s:
            f = await s.get(Finding, uuid.UUID(question_id))
            if f is None or f.org_id != _org():
                raise ValueError("вопрос не найден")
            share = {fl.line_id: fl.overpayment for fl in f.lines}
            lines = (
                await s.scalars(
                    select(PurchaseLine).where(PurchaseLine.id.in_(share)).order_by(PurchaseLine.date).limit(100)
                )
            ).all()
            return {
                **_question(f),
                "reference": _s(f.reference),
                "evidence": f.evidence,
                "decision_note": f.decision_note,
                "lines": [
                    {
                        "date": _s(x.date),
                        "doc": x.doc_no,
                        "row": x.source_row,
                        "supplier": x.supplier,
                        "item": x.item_raw,
                        "qty": _s(x.qty),
                        "unit": x.unit_raw,
                        "price": _s(x.price),
                        "overpayment": _s(share[x.id]),
                    }
                    for x in lines
                ],
            }

    @mcp.tool()
    async def price_history(sku: str, audit_id: str | None = None) -> dict[str, Any]:
        """История цен позиции (sku из вопроса): покупки по поставщикам и рыночные точки за период проверки."""
        async with session_scope() as s:
            run = await _resolve_run(s, audit_id)
            rows = (
                await s.scalars(
                    select(PurchaseLine)
                    .where(PurchaseLine.run_id == run.id, PurchaseLine.sku == sku)
                    .order_by(PurchaseLine.date)
                )
            ).all()
            market = (
                await s.scalars(select(MarketPoint).where(MarketPoint.sku == sku).order_by(MarketPoint.date).limit(300))
            ).all()
            return {
                "sku": sku,
                "unit": rows[0].unit_base if rows else None,
                "purchases": [
                    {
                        "date": _s(x.date),
                        "supplier": x.supplier,
                        "price": _s(x.price_base),
                        "qty": _s(x.qty_base),
                        "overpayment": _s(x.overpayment),
                    }
                    for x in rows
                ],
                "market": [{"date": _s(m.date), "price": _s(m.price), "source": m.source} for m in market],
            }

    @mcp.tool()
    async def submit_purchases(
        lines: list[dict[str, Any]], source: str = "mcp", period_start: str | None = None, period_end: str | None = None
    ) -> dict[str, Any]:
        """Отправить строки закупок (поля: date, doc, supplier, supplier_inn, item, unit, qty, price, amount, vat,
        object, responsible, ref) — запускается проверка. Нужен токен с правом «push»."""
        ctx = _token.get()
        if not ctx or "push" not in ctx[1]:
            raise PermissionError("у токена нет права «push» — выдайте его в настройках «Второго ключа»")
        try:
            payload = PurchasesIn(source=source, period_start=period_start, period_end=period_end, lines=lines)
        except ValidationError as exc:
            raise ValueError(f"строки не прошли проверку: {exc.errors()[:5]}") from exc
        from secondkey.services.integrations import submit_purchases as submit
        from secondkey.worker.tasks import run_audit_task

        async with session_scope() as s:
            run = await submit(s, ctx[0], payload)
            run_id = run.id
        await run_audit_task.kiq(str(run_id))
        return {
            "audit_id": str(run_id),
            "lines": len(payload.lines),
            "next": "проверьте статус инструментом audit_status",
        }

    @mcp.tool()
    async def audit_status(audit_id: str) -> dict[str, Any]:
        """Статус проверки: queued → importing → normalizing → analyzing → reporting → done | failed."""
        async with session_scope() as s:
            run = await _resolve_run(s, audit_id)
            return {**_run_dict(run), "progress": run.progress}

    @mcp.prompt()
    def export_purchases_from_1c(period_start: str, period_end: str) -> str:
        """Рецепт для агента: забрать поступления из 1С через MCP-сервер 1С и отправить во «Второй ключ»."""
        return (
            f"Выгрузи закупки материалов из 1С за {period_start} — {period_end} и отправь их во «Второй ключ».\n"
            "1. Через MCP-сервер 1С найди проведённые документы поступления: «ПоступлениеТоваровУслуг» (БП 3.0) "
            "или «ПриобретениеТоваровУслуг» (УТ 11, ERP), табличная часть «Товары».\n"
            "2. Для каждой строки собери: date (ГГГГ-ММ-ДД), doc («Поступление … № от ДД.ММ.ГГГГ»), supplier и "
            "supplier_inn (контрагент), item (наименование номенклатуры как в 1С), unit, qty, price, amount "
            "(сумма с НДС), vat, object (склад или объект), responsible (ответственный или менеджер), ref (GUID).\n"
            "3. Не меняй наименования и цены — проверка сравнивает их как есть.\n"
            "4. Отправь строки инструментом submit_purchases пачками до 5000 строк, затем дождись audit_status = done "
            "и покажи audit_summary."
        )

    return mcp


def mount(app) -> FastMCP:
    """Подключить MCP к приложению FastAPI под /api/mcp; сессионный менеджер запускается в lifespan."""
    mcp = build_mcp()
    app.mount("/api/mcp", TokenAuth(mcp.streamable_http_app()))
    app.state.mcp = mcp
    return mcp
