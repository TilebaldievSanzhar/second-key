"""Сквозной аудит на чистых данных: строки → нормализация → правила цены → связки → результат с итогом по строкам."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from secondkey.engine.importer import import_registry
from secondkey.engine.links import run_links
from secondkey.engine.market import MarketStore
from secondkey.engine.models import (
    AuditFinding,
    AuditResult,
    NormLine,
    PurchaseLine,
    StaffRef,
    SupplierCard,
    SupplierProfile,
)
from secondkey.engine.normalize import normalize
from secondkey.engine.rules import AuditConfig, run_all


def combine_overpayment(findings: list[AuditFinding]) -> dict[str, Decimal]:
    """Итог по строке — максимум вкладов разных правил: одна переплата не считается дважды."""
    per_line: dict[str, Decimal] = {}
    for f in findings:
        for lid, v in f.line_overpayment.items():
            if v > per_line.get(lid, Decimal("0")):
                per_line[lid] = v
    return per_line


def build_result(
    company: str,
    lines: list[PurchaseLine],
    norm: list[NormLine],
    findings: list[AuditFinding],
    warnings: list[str] | None = None,
    suppliers: list[SupplierProfile] | None = None,
) -> AuditResult:
    dates = [l.date for l in lines]
    return AuditResult(
        company=company,
        period=(min(dates), max(dates)) if dates else None,
        lines=norm,
        findings=sorted(findings, key=lambda f: (-f.overpayment, f.code)),
        line_overpayment=combine_overpayment(findings),
        warnings=list(warnings or []),
        suppliers=list(suppliers or []),
    )


def analyze(
    norm: list[NormLine],
    cfg: AuditConfig | None = None,
    market: MarketStore | None = None,
    cards: dict[str, SupplierCard] | None = None,
    staff: list[StaffRef] | None = None,
    hash_inn: Callable[[str], str] | None = None,
) -> tuple[list[AuditFinding], list[SupplierProfile]]:
    """Правила цены, затем связки: связкам нужны ценовые находки по строкам пары."""
    cfg = cfg or AuditConfig()
    findings = run_all(norm, cfg, market.lookup if market is not None else None)
    links, profiles = run_links(norm, findings, cfg, cards, staff, hash_inn)
    return findings + links, profiles


def audit_lines(
    lines: list[PurchaseLine],
    company: str,
    market: MarketStore | None = None,
    cfg: AuditConfig | None = None,
    warnings: list[str] | None = None,
    cards: dict[str, SupplierCard] | None = None,
    staff: list[StaffRef] | None = None,
    hash_inn: Callable[[str], str] | None = None,
) -> AuditResult:
    norm = normalize(lines)
    findings, profiles = analyze(norm, cfg, market, cards, staff, hash_inn)
    return build_result(company, lines, norm, findings, warnings, profiles)


def audit_file(
    path: str | Path,
    company: str,
    market: MarketStore | None = None,
    cfg: AuditConfig | None = None,
    staff: list[StaffRef] | None = None,
    hash_inn: Callable[[str], str] | None = None,
) -> AuditResult:
    imp = import_registry(path)
    return audit_lines(imp.lines, company, market, cfg, imp.warnings, imp.suppliers, staff, hash_inn)
