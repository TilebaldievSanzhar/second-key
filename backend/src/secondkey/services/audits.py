"""Сценарии аудита: приём файла, постановка прогона, выполнение по стадиям, сохранение результата.

Движок (secondkey.engine) — чистые функции; здесь IO: хранилище, БД, прогресс.
CPU-тяжёлые стадии движка идут в отдельном потоке, чтобы не блокировать event loop воркера.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from secondkey.config import settings
from secondkey.db.models import (
    AuditRun,
    Finding,
    FindingLine,
    MarketPoint,
    Organization,
    Payment,
    PurchaseLine,
    RunStatus,
    RunSupplier,
    Upload,
)
from secondkey.db.session import session_scope
from secondkey.engine.importer import import_payments, import_records, import_registry, preview_registry
from secondkey.engine.inn import inn_hash
from secondkey.engine.market import MarketPoint as EnginePoint
from secondkey.engine.market import MarketStore
from secondkey.engine.models import AuditFinding, NormLine, StaffRef, SupplierCard
from secondkey.engine.models import PurchaseLine as EngineLine
from secondkey.engine.normalize import normalize
from secondkey.engine.pipeline import analyze, build_result
from secondkey.engine.report import render_html
from secondkey.engine.rules import AuditConfig
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


def basic_report_key(report_key: str) -> str:
    """Отчёт без следов связок — для бухгалтера и наблюдателя (следы видят собственник и финансы)."""
    return report_key.removesuffix(".html") + "-basic.html"


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
        preview = asdict(await _with_tempfile(data, suffix, preview_registry))
    except Exception as exc:
        raise UserFacingError(f"Не удалось прочитать таблицу: {exc}") from exc
    preview["suggested"] = await _suggest(session, org_id, preview)

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
        preview=preview,
    )
    session.add(upload)
    await session.flush()
    return upload


def header_signature(columns: list[str]) -> str:
    """Отпечаток шапки выгрузки: та же форма отчёта из 1С → то же сопоставление колонок."""
    norm = "|".join(re.sub(r"\s+", " ", c).strip().lower() for c in columns)
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


async def _suggest(session: AsyncSession, org_id: uuid.UUID, preview: dict) -> dict | None:
    """Сохранённое сопоставление для такой же шапки, иначе — подсказка ИИ, если автопоиск не справился."""
    if preview.get("columns"):
        org = await session.get(Organization, org_id)
        saved = ((org.settings or {}).get("mapping_profiles") or {}).get(header_signature(preview["columns"]))
        if saved:
            return {**saved, "by": "saved"}
    if not preview.get("missing"):
        return None
    from secondkey import llm
    from secondkey.services import ai

    return await ai.suggest_mapping(preview) if llm.enabled() else None


async def remember_mapping(
    session: AsyncSession, org: Organization, upload: Upload, mapping: dict[str, int], header_row: int | None
) -> None:
    """Подтверждённое человеком сопоставление запоминается для следующих выгрузок той же формы."""
    columns = upload.preview.get("columns") or []
    if not columns or (mapping == upload.preview.get("mapping") and header_row == upload.preview.get("header_row")):
        return
    profiles = dict((org.settings or {}).get("mapping_profiles") or {})
    profiles[header_signature(columns)] = {"mapping": mapping, "header_row": header_row}
    org.settings = {**(org.settings or {}), "mapping_profiles": profiles}


async def create_json_upload(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    records: list[dict],
    source: str,
    extra: dict | None = None,
) -> Upload:
    """Строки закупок по API или из OData: хранятся как есть (JSON в S3) — для доказательства и повтора.

    extra — карточки поставщиков и сотрудники (хэши ИНН) из той же выгрузки: нужны связкам.
    """
    extra = {k: v for k, v in (extra or {}).items() if v}
    data = json.dumps({"source": source, "lines": records, **extra}, ensure_ascii=False).encode()
    upload_id = uuid.uuid4()
    dates = sorted(r["date"] for r in records if r.get("date"))
    name = f"{source} {dates[0]}–{dates[-1]}.json" if dates else f"{source}.json"
    key = f"org/{org_id}/uploads/{upload_id}/lines.json"
    await get_storage().put(key, data, "application/json")
    upload = Upload(
        id=upload_id,
        org_id=org_id,
        kind="json",
        filename=safe_filename(name),
        content_type="application/json",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        uploaded_by=user_id,
        preview={
            "header_row": 0,
            "columns": [],
            "mapping": {},
            "sample": [],
            "missing": [],
            "data_rows": len(records),
            "source": source,
            "suppliers": len(extra.get("suppliers", [])),
            "staff": len(extra.get("staff", [])),
        },
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
    # отметки времени — func.now(): часы БД, а не контейнера воркера (часы контейнеров расходятся)
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
        salt = (org.settings or {}).get("inn_salt") if org else None
        org_id = run.org_id

    try:
        await _set_stage(run_id, RunStatus.importing, 5, started_at=func.now(), error=None)
        data = await get_storage().get(upload.storage_key)
        cols = (mapping or {}).get("columns")
        hdr = (mapping or {}).get("header_row")
        payload: dict = {}
        try:
            if upload.kind == "json":
                payload = json.loads(data)
                imp = await asyncio.to_thread(import_records, payload["lines"])
            else:
                imp = await _with_tempfile(data, _suffix(upload.filename), import_registry, cols, hdr)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from exc
        cards, staff = _cards_and_staff(imp.suppliers, payload)
        payments = imp.payments + (await asyncio.to_thread(import_payments, payload.get("payments") or []))
        if not imp.lines:
            raise UserFacingError("В таблице не нашлось ни одной строки закупки с датой, поставщиком и количеством")

        await _set_stage(run_id, RunStatus.normalizing, 30)
        norm = await asyncio.to_thread(normalize, imp.lines)
        norm = await _ai_normalize(norm, imp.warnings)

        await _set_stage(run_id, RunStatus.analyzing, 55)
        from secondkey.services import network, registry

        async with session_scope() as s:
            market = await _market_for(s, {n.sku for n in norm if n.sku})
            reg = await registry.records_for(s, org_id, {n.line.supplier_inn for n in norm if n.line.supplier_inn})
            net = await network.stats_for(
                s,
                await s.get(Organization, org_id),
                {(n.line.supplier_inn, n.sku) for n in norm if n.line.supplier_inn and n.sku},
            )
        cfg = config_from_params(params)
        hash_fn = (lambda inn: inn_hash(salt, inn)) if salt and staff else None
        findings, suppliers = await asyncio.to_thread(
            analyze, norm, cfg, market if len(market) else None, cards, staff, hash_fn, payments, reg, net
        )
        result = build_result(company, imp.lines, norm, findings, imp.warnings, suppliers)

        await _set_stage(run_id, RunStatus.reporting, 80)
        await _persist(run_id, result, len(market), payments)
        html = await asyncio.to_thread(render_html, result, len(market))
        basic = await asyncio.to_thread(render_html, result, len(market), False)
        async with session_scope() as s:
            run = await s.get(AuditRun, run_id)
            report_key = f"org/{run.org_id}/runs/{run_id}/report.html"
        await get_storage().put(report_key, html.encode("utf-8"), "text/html; charset=utf-8")
        await get_storage().put(basic_report_key(report_key), basic.encode("utf-8"), "text/html; charset=utf-8")
        await _set_stage(run_id, RunStatus.done, 100, report_key=report_key, finished_at=func.now())
        log.info(
            "audit.done",
            run_id=str(run_id),
            lines=len(norm),
            findings=len(findings),
            overpayment=str(result.total_overpayment),
        )
    except UserFacingError as exc:
        await _set_stage(run_id, RunStatus.failed, 100, error=str(exc), finished_at=func.now())
    except Exception as exc:
        log.exception("audit.failed", run_id=str(run_id))
        await _set_stage(
            run_id,
            RunStatus.failed,
            100,
            finished_at=func.now(),
            error="Внутренняя ошибка при обработке. Мы уже видим её в журнале; попробуйте запустить ещё раз.",
        )
        raise exc


def _cards_and_staff(from_columns: dict[str, SupplierCard], payload: dict) -> tuple[dict, list[StaffRef]]:
    """Реквизиты поставщиков: из колонок таблицы или из карточек выгрузки 1С (карточки важнее)."""
    cards = dict(from_columns)
    for c in payload.get("suppliers") or []:
        name = re.sub(r"\s+", " ", str(c.get("name") or "")).strip()
        if name:
            cards[name] = SupplierCard(
                name=name,
                inn=c.get("inn"),
                phone=c.get("phone"),
                email=c.get("email"),
                address=c.get("address"),
                bank_account=c.get("bank_account"),
            )
    staff = [
        StaffRef(name=x["name"], inn_hash=x.get("inn_hash"), role=x.get("role"))
        for x in payload.get("staff") or []
        if x.get("name")
    ]
    return cards, staff


async def _ai_normalize(norm, warnings: list[str]):
    """Нераспознанные строки — модулю промптов normalize_item; позицию по-прежнему определяет движок."""
    from secondkey import llm
    from secondkey.services import ai

    if not llm.enabled() or not any(n.status == "needs_input" for n in norm):
        return norm
    try:
        norm, fixed = await ai.normalize_fallback(norm, settings.llm_max_normalize_lines)
    except Exception as exc:  # ИИ — улучшение, а не условие прогона
        log.warning("ai.normalize_failed", error=str(exc)[:300])
        return norm
    if fixed:
        warnings.append(f"ИИ распознал номенклатуру в {fixed} строках — они помечены «ИИ» в отчёте")
    return norm


def build_finding(org_id: uuid.UUID, run_id: uuid.UUID, f: AuditFinding, line_ids: dict, by_key: dict) -> Finding:
    """Находка движка → строка БД. Ответственный — из evidence (связка, аванс) или самый частый по строкам."""
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
        responsible=(f.evidence or {}).get("responsible") or (who.most_common(1)[0][0] if who else None),
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
    return finding


def engine_line(r: PurchaseLine) -> NormLine:
    """Строка из БД → нормализованная строка движка (для пересчёта связок без нового прогона)."""
    line = EngineLine(
        line_id=r.line_key,
        date=r.date,
        supplier=r.supplier,
        item_raw=r.item_raw,
        qty=r.qty,
        unit_raw=r.unit_raw,
        price=r.price,
        amount=r.amount,
        doc_no=r.doc_no,
        supplier_inn=r.supplier_inn,
        vat_included=r.vat_included,
        object_name=r.object_name,
        responsible=r.responsible,
        source_row=r.source_row,
        doc_in_no=r.doc_in_no,
        doc_in_date=r.doc_in_date,
    )
    return NormLine(
        line, r.sku, r.sku_label, r.head, r.unit_base, r.qty_base, r.price_base, r.norm_status, r.norm_note or ""
    )


def run_supplier_fields(p) -> dict:
    """Досье поставщика движка → поля RunSupplier (без org/run)."""
    return {
        "name": p.name,
        "inn": p.inn,
        "inn_kind": p.inn_kind,
        "inn_date": p.inn_date,
        "spend": p.spend,
        "lines": p.lines,
        "share": p.share,
        "first_date": p.first_date,
        "last_date": p.last_date,
        "skus": p.skus,
        "buyers": _jsonable(p.buyers),
        "card": _jsonable(asdict(p.card)) if p.card else None,
        "traces": [_jsonable({k: v for k, v in asdict(t).items() if k != "line_ids"}) for t in p.traces],
        "payment": _jsonable(p.payment) if p.payment else None,
        "registry_data": _jsonable(p.registry) if p.registry else None,
    }


async def _persist(run_id: uuid.UUID, result, market_points: int, payments: list | None = None) -> None:
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        org_id = run.org_id
        await s.execute(delete(Finding).where(Finding.run_id == run_id))
        await s.execute(delete(RunSupplier).where(RunSupplier.run_id == run_id))
        await s.execute(delete(Payment).where(Payment.run_id == run_id))
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
                    "doc_in_no": (L.doc_in_no or "")[:64] or None,
                    "doc_in_date": L.doc_in_date,
                }
            )
        for i in range(0, len(rows), 1000):
            await s.execute(insert(PurchaseLine), rows[i : i + 1000])

        by_key = {nl.line.line_id: nl for nl in result.lines}
        for f in result.findings:
            s.add(build_finding(org_id, run_id, f, line_ids, by_key))
        pay_rows = [
            {
                "id": uuid.uuid4(),
                "org_id": org_id,
                "run_id": run_id,
                "payment_key": x.payment_id or f"p{i}",
                "date": x.date,
                "supplier": x.supplier[:300],
                "supplier_inn": x.supplier_inn,
                "amount": x.amount,
                "doc_no": (x.doc_no or "")[:300] or None,
                "purpose": x.purpose,
            }
            for i, x in enumerate(payments or [], start=1)
        ]
        for i in range(0, len(pay_rows), 1000):
            await s.execute(insert(Payment), pay_rows[i : i + 1000])

        linked = {f.supplier for f in result.findings if f.code == "LINK"}
        for p in result.suppliers:
            s.add(RunSupplier(org_id=org_id, run_id=run_id, linked=p.name in linked, **run_supplier_fields(p)))

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
    if isinstance(v, date):
        return v.isoformat()
    return v
