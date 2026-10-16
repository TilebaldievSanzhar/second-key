"""Что можно узнать о поставщике из одного ИНН и из совместных заявок (агрегаты по выборке gz_bids_sample.py).

ИНН КР — 14 цифр: первая 0 — юрлицо, 1/2 — физлицо или ИП (пол); цифры 2–9 — дата ДДММГГГГ
(регистрации юрлица, рождения физлица). Скрипт проверяет, что дата разбирается и не позже заявки.
Запуск: python scripts/research/gz_inn_stats.py out.json
"""

import collections
import json
import sys
from datetime import date, datetime
from pathlib import Path


def parse_inn(inn: str | None) -> tuple[str, date | None] | None:
    if not inn or len(inn) != 14 or not inn.isdigit():
        return None
    kind = {"0": "юрлицо", "1": "физлицо, ж", "2": "физлицо, м"}.get(inn[0], "другое")
    try:
        return kind, date(int(inn[5:9]), int(inn[3:5]), int(inn[1:3]))
    except ValueError:
        return kind, None


def bid_date(row: dict) -> date | None:
    try:
        return datetime.fromisoformat(str(row.get("date")).replace(" ", "T")[:19]).date()
    except ValueError:
        return None


def buckets(values: list[float], edges: list[tuple[int, int]]) -> dict[str, int]:
    return {f"{a}–{b}": sum(1 for v in values if a <= v < b) for a, b in edges}


def main(path: Path) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    suppliers: dict[str, dict] = {}
    for r in rows:
        if r["inn"]:
            suppliers.setdefault(r["inn"], r)
    print(f"заявок {len(rows)}, тендеров {len({r['tender'] for r in rows})}, поставщиков с ИНН {len(suppliers)}")

    kinds: collections.Counter = collections.Counter()
    bad = later = 0
    org_age: list[float] = []
    person_age: list[float] = []
    for inn, r in suppliers.items():
        parsed = parse_inn(inn)
        if not parsed or parsed[1] is None:
            bad += 1
            continue
        kind, d = parsed
        kinds[kind] += 1
        on = bid_date(r) or date.today()
        later += d > on
        (org_age if kind == "юрлицо" else person_age).append((on - d).days / 365.25)
    print("типы:", dict(kinds), "| дата не разбирается:", bad, "| дата позже заявки:", later)
    print("юрлица, лет с регистрации:", buckets(org_age, [(0, 1), (1, 2), (2, 5), (5, 10), (10, 40)]))
    print("физлица и ИП, возраст:", buckets(person_age, [(0, 23), (23, 30), (30, 45), (45, 60), (60, 120)]))

    # один подавший за нескольких участников и пары, которые часто встречаются в одних тендерах
    by_creator: dict[str, set] = collections.defaultdict(set)
    by_tender: dict[str, set] = collections.defaultdict(set)
    for r in rows:
        if r["creator"] and r["inn"]:
            by_creator[r["creator"]].add(r["inn"])
        if r["inn"]:
            by_tender[r["tender"]].add(r["inn"])
    print("подавших, представлявших ≥ 2 компании:", sum(1 for v in by_creator.values() if len(v) >= 2))
    pairs: collections.Counter = collections.Counter()
    for inns in by_tender.values():
        s = sorted(inns)
        pairs.update((a, b) for i, a in enumerate(s) for b in s[i + 1 :])
    print("пар, встречавшихся вместе ≥ 2 раз:", sum(1 for v in pairs.values() if v >= 2))
    print("пар, встречавшихся вместе ≥ 3 раз:", sum(1 for v in pairs.values() if v >= 3))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
