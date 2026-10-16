"""Выборка заявок по стройматериалам с портала госзакупок КР: ИНН участника, тендер, дата, подавший.

ФИО подавших не сохраняются — только короткий хэш для поиска совпадений; в отчёт идут агрегаты.
Запуск: python scripts/research/gz_bids_sample.py out.json
"""

import hashlib
import json
import ssl
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

BASE = "https://goszakupki.okmot.kg"
QUERIES = (
    "арматура",
    "цемент",
    "бетон",
    "кирпич",
    "песок",
    "щебень",
    "строительные материалы",
    "краска",
    "трубы",
    "пиломатериалы",
    "ремонт",
    "строительство",
)

# системное хранилище сертификатов: у портала неполная цепочка, certifi её не собирает
http = httpx.Client(timeout=60, verify=ssl.create_default_context(), headers={"User-Agent": "second-key research"})


def get(path: str):
    time.sleep(0.5)
    r = http.get(BASE + path)
    r.raise_for_status()
    return r.json()


def main(out: Path) -> None:
    rows: list[dict] = []
    tenders: set = set()
    for q in QUERIES:
        for first in (0, 50):
            try:
                res = get(f"/api/public_search/search/published?q={urllib.parse.quote(q)}&first={first}&rows=50")
            except httpx.HTTPError:
                continue
            for t in res.get("content", []):
                tid = t.get("id")
                if tid in tenders:
                    continue
                tenders.add(tid)
                try:
                    subs = get(f"/api/public_submission/getSubmissionsbyTenderId?tenderId={tid}")
                except httpx.HTTPError:
                    continue
                for s in subs if isinstance(subs, list) else []:
                    who = (s.get("creatorFullName") or "").strip().upper()
                    rows.append(
                        {
                            "tender": tid,
                            "inn": s.get("companyInn"),
                            "company": s.get("companyName"),
                            "creator": hashlib.sha256(who.encode()).hexdigest()[:16] if who else None,
                            "date": s.get("dateContest") or s.get("dateComplated"),
                        }
                    )
        print(q, len(tenders), len(rows), flush=True)
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
