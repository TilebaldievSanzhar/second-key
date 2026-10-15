"""Сценарии аудита: приём файла, постановка прогона, выполнение по стадиям, сохранение результата.

Движок (secondkey.engine) — чистые функции; здесь IO: хранилище, БД, прогресс.
CPU-тяжёлые стадии движка идут в отдельном потоке, чтобы не блокировать event loop воркера.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.db.models import (
    AuditRun,
    Finding,
    FindingLine,
    MarketPoint,
    Organization,
    PurchaseLine,
    RunStatus,
    Upload,
)
from secondkey.db.session import session_scope
from secondkey.engine.importer import import_registry, preview_registry
from secondkey.engine.market import MarketPoint as EnginePoint
from secondkey.engine.market import MarketStore
from secondkey.engine.normalize import normalize
from secondkey.engine.pipeline import build_result
from secondkey.engine.report import render_html
from secondkey.engine.rules import AuditConfig, run_all
from secondkey.observability import log
from secondkey.services import progress
from secondkey.services.storage import get_storage

ALLOWED_SUFFIXES = {".xlsx", ".xlsm", ".csv"}

# пороги правил по умолчанию — политика закупок компании, меняется в настройках организации
DEFAULT_PARAMS: dict[str, str | int] = {
    "approval_threshold": "500000",
    "parallel_window_days": 30,
    "parallel_tolerance": "0.05",
    "market_tolerance": "0.10",
    "market_window_days": 60,
}


class UserFacingError(Exception):
    """Ошибка с текстом для пользователя: что не так и как исправить."""


def safe_filename(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\-() ]+", "_", name)[:150] or "file"


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


async def _with_tempfile(data: bytes, suffix: str, fn, *args):
    def run():
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"upload{suffix}"
            p.write_bytes(data)
            return fn(p, *args)

    return await asyncio.to_thread(run)


async def create_upload(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, filename: str, content_type: str | None, data: bytes
) -> Upload:
    suffix = _suffix(filename)
    if suffix not in ALLOWED_SUFFIXES:
        raise UserFacingError("Нужен файл Excel (.xlsx) или CSV — выгрузка закупок из 1С или таблица")
    if not data:
        raise UserFacingError("Файл пустой")
    try:
        preview = await _with_tempfile(data, suffix, preview_registry)
    except Exception as exc:
        raise UserFacingError(f"Не удалось прочитать таблицу: {exc}") from exc

    upload_id = uuid.uuid4()
    key = f"org/{org_id}/uploads/{upload_id}/{safe_filename(filename)}"
    await get_storage().put(key, data, content_type)
    upload = Upload(
        id=upload_id,
        org_id=org_id,
        filename=safe_filename(filename),
        content_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        uploaded_by=user_id,
        preview=asdict(preview),
    )
    session.add(upload)
    await session.flush()
    return upload


def run_params(org: Organization) -> dict[str, Any]:
    return {**DEFAULT_PARAMS, **{k: v for k, v in (org.settings or {}).items() if k in DEFAULT_PARAMS}}


def config_from_params(params: dict[str, Any]) -> AuditConfig:
    cfg = AuditConfig()
    for k, v in params.items():
        if hasattr(cfg, k):
            current = getattr(cfg, k)
            setattr(cfg, k, Decimal(str(v)) if isinstance(current, Decimal) else int(v))
    return cfg


async def create_run(
    session: AsyncSession,
    org: Organization,
    upload: Upload,
    user_id: uuid.UUID,
    mapping: dict[str, int] | None = None,
    header_row: int | None = None,
) -> AuditRun:
    run = AuditRun(
        org_id=org.id,
        upload_id=upload.id,
        params=run_params(org),
        created_by=user_id,
        mapping={"columns": mapping, "header_row": header_row} if mapping else None,
    )
    session.add(run)
    await session.flush()
    return run


async def _set_stage(run_id: uuid.UUID, status: RunStatus, progress_pct: int, **fields: Any) -> None:
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        if run is None:
            return
        run.status, run.progress = status, progress_pct
        for k, v in fields.items():
            setattr(run, k, v)
    await progress.publish(
        run_id,
        {"status": status.value, "progress": progress_pct, **{k: v for k, v in fields.items() if k in ("error",)}},
    )


async def _market_for(session: AsyncSession, skus: set[str]) -> MarketStore:
    if not skus:
        return MarketStore([])
    rows = (await session.execute(select(MarketPoint).where(MarketPoint.sku.in_(skus)))).scalars().all()
    return MarketStore(
        [
            EnginePoint(
                sku=r.sku,
                date=r.date,
                price=r.price,
                unit=r.unit,
                source=r.source,
                region=r.region,
                ref=r.ref,
                label=r.label,
            )
            for r in rows
        ]
    )


async def execute_run(run_id: uuid.UUID) -> None:
    """Выполнение прогона. Идемпотентно: повтор пересобирает результат прогона с нуля."""
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        if run is None:
            log.warning("audit.run_missing", run_id=str(run_id))
            return
        org = await s.get(Organization, run.org_id)
        upload = run.upload
        params, mapping = dict(run.params), run.mapping
        company = org.name if org else "Компания"

    try:
        await _set_stage(run_id, RunStatus.importing, 5, started_at=datetime.now(UTC), error=None)
        data = await get_storage().get(upload.storage_key)
        cols = (mapping or {}).get("columns")
        hdr = (mapping or {}).get("header_row")
        try:
            imp = await _with_tempfile(data, _suffix(upload.filename), import_registry, cols, hdr)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from exc
        if not imp.lines:
            raise UserFacingError("В таблице не нашлось ни одной строки закупки с датой, поставщиком и количеством")

        await _set_stage(run_id, RunStatus.normalizing, 30)
        norm = await asyncio.to_thread(normalize, imp.lines)

        await _set_stage(run_id, RunStatus.analyzing, 55)
        async with session_scope() as s:
            market = await _market_for(s, {n.sku for n in norm if n.sku})
        cfg = config_from_params(params)
        findings = await asyncio.to_thread(run_all, norm, cfg, market.lookup if len(market) else None)
        result = build_result(company, imp.lines, norm, findings, imp.warnings)

        await _set_stage(run_id, RunStatus.reporting, 80)
        await _persist(run_id, result, len(market))
        html = await asyncio.to_thread(render_html, result, len(market))
        async with session_scope() as s:
            run = await s.get(AuditRun, run_id)
            report_key = f"org/{run.org_id}/runs/{run_id}/report.html"
        await get_storage().put(report_key, html.encode("utf-8"), "text/html; charset=utf-8")
        await _set_stage(run_id, RunStatus.done, 100, report_key=report_key, finished_at=datetime.now(UTC))
        log.info(
            "audit.done",
            run_id=str(run_id),
            lines=len(norm),
            findings=len(findings),
            overpayment=str(result.total_overpayment),
        )
    except UserFacingError as exc:
        await _set_stage(run_id, RunStatus.failed, 100, error=str(exc), finished_at=datetime.now(UTC))
    except Exception as exc:
        log.exception("audit.failed", run_id=str(run_id))
        await _set_stage(
            run_id,
            RunStatus.failed,
            100,
            finished_at=datetime.now(UTC),
            error="Внутренняя ошибка при обработке. Мы уже видим её в журнале; попробуйте запустить ещё раз.",
        )
        raise exc


async def _persist(run_id: uuid.UUID, result, market_points: int) -> None:
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        org_id = run.org_id
        await s.execute(delete(Finding).where(Finding.run_id == run_id))
        await s.execute(delete(PurchaseLine).where(PurchaseLine.run_id == run_id))

        line_ids: dict[str, uuid.UUID] = {}
        rows = []
        for nl in result.lines:
            L = nl.line
            lid = uuid.uuid4()
            line_ids[L.line_id] = lid
            rows.append(
                {
                    "id": lid,
                    "org_id": org_id,
                    "run_id": run_id,
                    "line_key": L.line_id,
                    "source_row": L.source_row,
                    "date": L.date,
                    "doc_no": L.doc_no,
                    "supplier": L.supplier,
                    "supplier_inn": L.supplier_inn,
                    "item_raw": L.item_raw,
                    "qty": L.qty,
                    "unit_raw": L.unit_raw,
                    "price": L.price,
                    "amount": L.amount,
                    "vat_included": L.vat_included,
                    "object_name": L.object_name,
                    "responsible": L.responsible,
                    "sku": nl.sku,
                    "sku_label": nl.sku_label,
                    "head": nl.head,
                    "unit_base": nl.unit,
                    "qty_base": nl.qty,
                    "price_base": nl.price,
                    "norm_status": nl.status,
                    "norm_note": nl.note or None,
                    "overpayment": result.line_overpayment.get(L.line_id, Decimal("0")),
                }
            )
        for i in range(0, len(rows), 1000):
            await s.execute(insert(PurchaseLine), rows[i : i + 1000])

        by_key = {nl.line.line_id: nl for nl in result.lines}
        for f in result.findings:
            who = Counter(by_key[l].line.responsible for l in f.line_ids if l in by_key and by_key[l].line.responsible)
            finding = Finding(
                org_id=org_id,
                run_id=run_id,
                code=f.code,
                severity=f.severity,
                title=f.title,
                message=f.message,
                sku=f.sku,
                sku_label=f.sku_label,
                supplier=f.supplier,
                responsible=who.most_common(1)[0][0] if who else None,
                reference=f.reference,
                reference_kind=f.reference_kind,
                overpayment=f.overpayment,
                evidence=_jsonable(f.evidence),
            )
            finding.lines = [
                FindingLine(line_id=line_ids[l], overpayment=f.line_overpayment.get(l, Decimal("0")))
                for l in dict.fromkeys(f.line_ids)
                if l in line_ids
            ]
            s.add(finding)

        statuses = Counter(nl.status for nl in result.lines)
        run.period_start, run.period_end = result.period if result.period else (None, None)
        run.lines_total = len(result.lines)
        run.lines_ok = statuses.get("ok", 0)
        run.lines_needs_input = statuses.get("needs_input", 0)
        run.spend = result.total_spend
        run.overpayment = result.total_overpayment
        run.findings_count = len(result.findings)
        run.market_points = market_points
        run.warnings = result.warnings[:200]


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    return v
