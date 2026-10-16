"""Бенчмарк аудита закупок (v11-audit) на синтетическом реестре с ground truth.

Меряет три вещи:
1. нормализацию: какая доля строк распознана и не склеены ли разные позиции
   (попарные precision/recall отношения «та же позиция»);
2. обнаружение: какие подсаженные нарушения найдены и сколько ложных срабатываний;
3. деньги: оценка переплаты против истинной переплаты по типам нарушений;
4. связки: найдены ли подсаженные пары «снабженец — поставщик», их следы и лишние связки.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from secondkey.engine.inn import inn_hash
from secondkey.engine.market import MarketPoint, MarketStore
from secondkey.engine.models import AuditResult, PurchaseLine, StaffRef
from secondkey.engine.normalize import normalize_line
from secondkey.engine.pipeline import audit_file
from secondkey.engine.rules import AuditConfig

MONEY_TYPES = ("A1", "A2", "A3", "A8", "B1", "C1")
BENCH_SALT = "bench"


def load_market(raw_path: Path) -> MarketStore:
    """Рыночные точки → позиции тем же нормализатором, что и строки клиента."""
    from datetime import date

    points = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        price = Decimal(r["price"])
        pl = PurchaseLine(
            line_id="m",
            date=date.fromisoformat(r["date"]),
            supplier="market",
            item_raw=r["name"],
            qty=Decimal("1"),
            unit_raw=r["unit"],
            price=price,
            amount=price,
        )
        nl = normalize_line(pl)
        if nl.status != "ok":
            continue
        points.append(
            MarketPoint(sku=nl.sku, date=pl.date, price=nl.price, unit=nl.unit, source=r["source"], label=nl.sku_label)
        )
    return MarketStore(points)


def _pairs_metrics(res: AuditResult, truth_lines: dict) -> dict:
    pred = defaultdict(list)
    true = defaultdict(list)
    norm_ids = []
    for nl in res.lines:
        if nl.sku is None:
            continue
        norm_ids.append(nl.line_id)
        pred[nl.sku].append(nl.line_id)
        true[truth_lines[nl.line_id]["sku"]].append(nl.line_id)
    tsku = {lid: truth_lines[lid]["sku"] for lid in norm_ids}
    psku = {nl.line_id: nl.sku for nl in res.lines if nl.sku}
    pred_pairs = sum(len(v) * (len(v) - 1) // 2 for v in pred.values())
    true_pairs = sum(len(v) * (len(v) - 1) // 2 for v in true.values())
    both = 0
    for ids in pred.values():
        cnt = defaultdict(int)
        for lid in ids:
            cnt[tsku[lid]] += 1
        both += sum(c * (c - 1) // 2 for c in cnt.values())
    # склейки: пары разных истинных позиций в одной предсказанной группе
    merged = []
    for sku, ids in pred.items():
        kinds = {tsku[l] for l in ids}
        if len(kinds) > 1:
            merged.append({"sku": sku, "true": sorted(kinds)})
    split = {k: sorted({psku[l] for l in v}) for k, v in true.items() if len({psku[l] for l in v}) > 1}
    return {
        "pair_precision": round(both / pred_pairs, 4) if pred_pairs else None,
        "pair_recall": round(both / true_pairs, 4) if true_pairs else None,
        "merged_groups": merged,
        "split_true_skus": {k: len(v) for k, v in split.items()},
    }


def _links_metrics(res: AuditResult, truth_links: dict) -> dict:
    """Подсаженные связки: найдена ли пара, какие ожидаемые следы есть; связки сверх подсаженных — ложные."""
    found = {(f.supplier, f.evidence.get("responsible")): f for f in res.findings if f.code == "LINK"}
    expected_pairs = {(v["supplier"], v["responsible"]) for v in truth_links.values()}
    detail, hit, traces_total, traces_hit = {}, 0, 0, 0
    for key, v in truth_links.items():
        f = found.get((v["supplier"], v["responsible"]))
        codes = {t["code"] for t in f.evidence["traces"]} if f else set()
        got = [c for c in v["traces"] if c in codes]
        hit += int(f is not None)
        traces_total += len(v["traces"])
        traces_hit += len(got)
        detail[key] = {"found": f is not None, "traces": sorted(codes), "missing": sorted(set(v["traces"]) - codes)}
    extra = sorted(f"{s} / {r}" for s, r in found if (s, r) not in expected_pairs)
    return {
        "planted": len(truth_links),
        "detected": hit,
        "recall": round(hit / len(truth_links), 4) if truth_links else None,
        "trace_recall": round(traces_hit / traces_total, 4) if traces_total else None,
        "false_links": extra,
        "detail": detail,
    }


def run(corpus_dir: Path, out_dir: Path | None = None, overrides: dict | None = None) -> tuple:
    truth = json.loads((corpus_dir / "truth.json").read_text(encoding="utf-8"))
    market = load_market(corpus_dir / "market_raw.jsonl")
    cfg = AuditConfig(approval_threshold=Decimal(truth["approval_threshold"]), **(overrides or {}))
    staff = [
        StaffRef(name=x["name"], role=x.get("role"), inn_hash=inn_hash(BENCH_SALT, x["inn"]))
        for x in truth.get("staff", [])
    ]
    res = audit_file(
        corpus_dir / "registry.xlsx",
        truth["company"],
        market,
        cfg,
        staff=staff,
        hash_inn=lambda inn: inn_hash(BENCH_SALT, inn),
    )
    tl = truth["lines"]

    status = defaultdict(int)
    for nl in res.lines:
        status[nl.status] += 1
    norm = {
        "lines": len(res.lines),
        "status": dict(status),
        "share_auto": round((status["ok"] + status["unit_error"]) / len(res.lines), 4),
        **_pairs_metrics(res, tl),
    }

    flagged = {lid for lid, v in res.line_overpayment.items() if v > 0}
    by_rule_lines: dict[str, set[str]] = defaultdict(set)
    for f in res.findings:
        for lid in f.line_ids:
            by_rule_lines[f.code].add(lid)

    # обнаружение по экземплярам нарушений
    detection = defaultdict(
        lambda: {"instances": 0, "detected": 0, "true_over": Decimal("0"), "found_over": Decimal("0")}
    )
    missed = []
    for inst_id, inst in truth["instances"].items():
        t = inst["type"]
        ids = inst["line_ids"]
        d = detection[t]
        d["instances"] += 1
        d["true_over"] += Decimal(inst["true_over"])
        if t in MONEY_TYPES:
            hit = sum(1 for l in ids if l in flagged)
            ok = hit / len(ids) >= 0.5
            d["found_over"] += sum((res.line_overpayment.get(l, Decimal("0")) for l in ids), Decimal("0"))
        elif t == "A5":
            ok = any(set(ids) <= set(f.line_ids) or ids[0] in f.line_ids for f in res.findings if f.code == "P-06")
            d["found_over"] += sum((res.line_overpayment.get(l, Decimal("0")) for l in ids), Decimal("0"))
        elif t == "A4":
            ok = any(len(set(f.line_ids) & set(ids)) >= 3 for f in res.findings if f.code == "P-05")
        elif t == "A6":
            ok = all(l in by_rule_lines["P-07"] for l in ids)
        else:
            ok = False
        d["detected"] += int(ok)
        if not ok:
            missed.append(inst_id)

    anomaly_lines = {
        lid
        for lid, v in tl.items()
        if any(a.split("-")[0] in MONEY_TYPES or a.startswith("A5") for a in v["anomalies"])
    }
    fp_lines = flagged - anomaly_lines
    fp_money = sum((res.line_overpayment[l] for l in fp_lines), Decimal("0"))
    proc_fp = {
        "P-05": sum(
            1
            for f in res.findings
            if f.code == "P-05" and not any(a.startswith("A4") for l in f.line_ids for a in tl[l]["anomalies"])
        ),
        "P-06": sum(
            1
            for f in res.findings
            if f.code == "P-06" and not any(a.startswith("A5") for l in f.line_ids for a in tl[l]["anomalies"])
        ),
        "P-07": sum(
            1
            for f in res.findings
            if f.code == "P-07" and not any(a.startswith("A6") for l in f.line_ids for a in tl[l]["anomalies"])
        ),
    }
    true_total = sum((Decimal(v["true_over"]) for v in tl.values()), Decimal("0"))
    found_on_true = sum((res.line_overpayment.get(l, Decimal("0")) for l in anomaly_lines), Decimal("0"))

    metrics = {
        "corpus": str(corpus_dir),
        "lines": len(res.lines),
        "spend": str(res.total_spend),
        "market_points": len(market),
        "normalization": norm,
        "detection": {
            t: {
                "instances": v["instances"],
                "detected": v["detected"],
                "recall": round(v["detected"] / v["instances"], 4),
                "true_over": str(v["true_over"]),
                "found_over": str(v["found_over"]),
                "money_ratio": (round(float(v["found_over"] / v["true_over"]), 4) if v["true_over"] else None),
            }
            for t, v in sorted(detection.items())
        },
        "missed_instances": missed,
        "money": {
            "true_total": str(true_total),
            "found_total": str(res.total_overpayment),
            "found_on_anomaly_lines": str(found_on_true),
            "ratio_found_to_true": round(float(found_on_true / true_total), 4) if true_total else None,
            "flagged_lines": len(flagged),
            "false_positive_lines": len(fp_lines),
            "line_precision": round(1 - len(fp_lines) / len(flagged), 4) if flagged else None,
            "false_positive_money": str(fp_money),
            "fp_money_share": round(float(fp_money / res.total_overpayment), 4) if res.total_overpayment else None,
        },
        "process_false_positives": proc_fp,
        "findings_by_rule": {
            c: sum(1 for f in res.findings if f.code == c) for c in sorted({f.code for f in res.findings})
        },
        "fp_examples": sorted(fp_lines)[:15],
        "links": _links_metrics(res, truth.get("links", {})),
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics, res, market
