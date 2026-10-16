"""Пересчёт связок прогона по свежим сведениям (реестр, сеть) — без нового прогона и без потери ответов.

Цены, оплаты и строки не меняются: берутся из БД. Меняются только следы и находки LINK:
- пара уже была вопросом — обновляются текст, следы и строки, а статус, ответ снабжения и решение остаются;
- новая пара — новый вопрос;
- пара больше не сходится — вопрос удаляется, только если на него ещё никто не ответил; иначе остаётся.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select

from secondkey.db.models import (
    AuditRun,
    Finding,
    FindingLine,
    FindingStatus,
    Organization,
    Payment,
    PurchaseLine,
    RunSupplier,
)
from secondkey.db.session import session_scope
from secondkey.engine.inn import inn_hash
from secondkey.engine.links import PRICE_RULES, run_links
from secondkey.engine.models import AuditFinding, SupplierCard
from secondkey.engine.payments import Payment as EnginePayment
from secondkey.engine.payments import profiles
from secondkey.services import network, registry
from secondkey.services.audits import (
    _cards_and_staff,
    build_finding,
    config_from_params,
    engine_line,
    run_supplier_fields,
)
from secondkey.services.storage import get_storage

CARD_FIELDS = ("phone", "email", "address", "bank_account")


async def refresh_links(run_id: uuid.UUID) -> dict:
    async with session_scope() as s:
        run = await s.get(AuditRun, run_id)
        if run is None:
            return {"links": 0}
        org = await s.get(Organization, run.org_id)
        rows = (await s.scalars(select(PurchaseLine).where(PurchaseLine.run_id == run_id))).all()
        norm = [engine_line(r) for r in rows]
        key_of = {r.id: r.line_key for r in rows}
        price = []
        for f in (
            await s.scalars(select(Finding).where(Finding.run_id == run_id, Finding.code.in_(PRICE_RULES)))
        ).all():
            over = {key_of[fl.line_id]: fl.overpayment for fl in f.lines if fl.line_id in key_of}
            price.append(
                AuditFinding(
                    code=f.code,
                    severity=f.severity,
                    title=f.title,
                    message=f.message,
                    line_ids=list(over),
                    overpayment=f.overpayment,
                    supplier=f.supplier,
                    line_overpayment=over,
                )
            )
        sups = (await s.scalars(select(RunSupplier).where(RunSupplier.run_id == run_id))).all()
        cards = {
            x.name: SupplierCard(name=x.name, inn=x.inn, **{k: (x.card or {}).get(k) for k in CARD_FIELDS})
            for x in sups
            if x.card
        }
        pays = [
            EnginePayment(
                date=p.date,
                supplier=p.supplier,
                amount=p.amount,
                supplier_inn=p.supplier_inn,
                doc_no=p.doc_no,
                purpose=p.purpose,
                payment_id=p.payment_key,
            )
            for p in (await s.scalars(select(Payment).where(Payment.run_id == run_id))).all()
        ]
        reg = await registry.records_for(s, run.org_id, {r.supplier_inn for r in rows if r.supplier_inn})
        net = await network.stats_for(s, org, {(r.supplier_inn, r.sku) for r in rows if r.supplier_inn and r.sku})
        cfg = config_from_params(dict(run.params))
        salt = (org.settings or {}).get("inn_salt") if org else None
        upload = run.upload
    staff = []
    if upload.kind == "json":
        payload = json.loads(await get_storage().get(upload.storage_key))
        _, staff = _cards_and_staff({}, payload)
    hash_fn = (lambda inn: inn_hash(salt, inn)) if salt and staff else None
    dates = [n.line.date for n in norm] + [p.date for p in pays]
    profs = profiles(norm, pays, cfg, max(dates)) if pays and dates else {}
    links, suppliers = run_links(norm, price, cfg, cards, staff, hash_fn, profs, reg, net)

    added = updated = removed = 0
    async with session_scope() as s:
        line_ids = {
            r.line_key: r.id for r in (await s.scalars(select(PurchaseLine).where(PurchaseLine.run_id == run_id))).all()
        }
        by_key = {n.line.line_id: n for n in norm}
        existing = {
            (f.supplier, (f.evidence or {}).get("responsible")): f
            for f in (await s.scalars(select(Finding).where(Finding.run_id == run_id, Finding.code == "LINK"))).all()
        }
        seen = set()
        for f in links:
            k = (f.supplier, f.evidence.get("responsible"))
            seen.add(k)
            new = build_finding(run.org_id, run_id, f, line_ids, by_key)
            old = existing.get(k)
            if old is None:
                s.add(new)
                added += 1
                continue
            for attr in ("severity", "message", "sku", "sku_label", "overpayment", "evidence", "responsible"):
                setattr(old, attr, getattr(new, attr))
            want = {fl.line_id: fl.overpayment for fl in new.lines}
            for fl in list(old.lines):  # строки вопроса — на месте: удалить лишние, добавить новые
                if fl.line_id in want:
                    fl.overpayment = want.pop(fl.line_id)
                else:
                    old.lines.remove(fl)
            old.lines.extend(FindingLine(line_id=lid, overpayment=v) for lid, v in want.items())
            updated += 1
        kept = set()
        for k, old in existing.items():
            if k in seen:
                continue
            if old.status == FindingStatus.open and not old.answer:
                await s.delete(old)
                removed += 1
            else:
                kept.add(k[0])  # на вопрос уже ответили — он остаётся, даже если следы больше не сходятся
        linked = {f.supplier for f in links}
        by_name = {x.name: x for x in (await s.scalars(select(RunSupplier).where(RunSupplier.run_id == run_id))).all()}
        for p in suppliers:
            row = by_name.get(p.name)
            if row is None:
                continue
            fields = run_supplier_fields(p)
            row.traces, row.registry_data, row.payment = fields["traces"], fields["registry_data"], fields["payment"]
            row.linked = p.name in linked or p.name in kept
        await s.flush()
        run = await s.get(AuditRun, run_id)
        run.findings_count = await s.scalar(select(func.count()).where(Finding.run_id == run_id)) or 0
    return {"links": len(links), "added": added, "updated": updated, "removed": removed}
