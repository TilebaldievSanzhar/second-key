"""Сборщик рыночных цен стройматериалов с нового портала госзакупок КР (goszakupki.okmot.kg).

Открытый JSON API отдаёт построчно: код ОКГЗ, характеристику, единицу, количество,
плановую цену за единицу (оценка заказчика) и ставки участников (цена за единицу,
страна происхождения). Это эталон рынка с доставкой (обычно DDP) по регионам КР.

Вежливый режим: пауза между запросами, кэш ответов на диске (повторный прогон
портал не трогает), только публичные эндпоинты.

collect() возвращает записи как есть; records_to_points() нормализует их к позициям
движка. Сохранение точек — в БД (services/market.py), здесь только сеть и разбор.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from secondkey.config import settings
from secondkey.engine.market import MarketPoint
from secondkey.engine.models import PurchaseLine
from secondkey.engine.normalize import normalize_line

BASE = "https://goszakupki.okmot.kg"
# запросы по названию закупки: стройматериалы и ремонт дают многопозиционные закупки
QUERIES = [
    "строительные материалы",
    "стройматериалы",
    "ремонт",
    "цемент",
    "арматура",
    "бетон",
    "песок",
    "щебень",
    "кирпич",
    "газоблок",
    "труба",
    "кабель",
    "гипсокартон",
    "краска",
    "шпаклевка",
    "штукатурка",
    "доска",
    "профнастил",
    "утеплитель",
    "сетка",
    "электроды",
    "раствор",
]
# разделы ОКГЗ (CPV) со стройматериалами: 44 — конструкции и материалы, 142 — песок, щебень,
# 3132/3134/3135 — кабели и провода, 0341 — древесина; остальное (шины, бланки) в эталон не идёт
CONSTRUCTION_OKGZ_PREFIXES = ("44", "142", "3132", "3134", "3135", "0341")
PRICED_STATUSES = {"PROTOCOL_OPENED", "EVALUATION_COMPLATED", "CONTRACT_SIGNED", "VERIFIED_PUBLISHED"}


class Client:
    def __init__(self, delay_s: float = 0.7, cache: Path | None = None):
        self.delay_s = delay_s
        self.cache = cache or settings.market_cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        # системное хранилище сертификатов: у портала неполная цепочка, certifi её не собирает
        self.http = httpx.Client(
            timeout=60, headers={"User-Agent": settings.http_user_agent}, verify=ssl.create_default_context()
        )
        self.requests = 0

    def get(self, path: str):
        key = hashlib.sha1(path.encode("utf-8")).hexdigest()
        f = self.cache / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        for attempt in range(3):
            try:
                time.sleep(self.delay_s)
                r = self.http.get(BASE + path)
                self.requests += 1
                r.raise_for_status()
                data = r.json()
                f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))

    def search(self, q: str, first: int = 0, rows: int = 50) -> list[dict]:
        r = self.get(f"/api/public_search/search/published?q={urllib.parse.quote(q)}&first={first}&rows={rows}")
        return r.get("content", []) if isinstance(r, dict) else []

    def tender(self, tid: str) -> dict:
        return self.get(f"/api/public_tender/get/{tid}")

    def submissions(self, tid: str) -> list[dict]:
        r = self.get(f"/api/public_submission/getSubmissionsbyTenderId?tenderId={tid}")
        return r if isinstance(r, list) else []


def _d(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T")[:19]).date().isoformat()
    except ValueError:
        return None


@dataclass
class Record:
    source: str  # kg_goszakupki:plan | kg_goszakupki:bid
    tender_id: str
    tender_number: str | None
    date: str
    region: str | None
    buyer: str | None
    okgz: str | None
    okgz_name: str | None
    spec: str | None
    unit: str | None
    qty: str | None
    price: str
    supplier: str | None = None
    origin: str | None = None


def parse_tender(meta: dict, tender: dict, subs: list[dict]) -> list[Record]:
    """Плановые цены по строкам закупки + цены ставок участников по тем же строкам."""
    out: list[Record] = []
    region = (meta.get("ate") or {}).get("name")
    d = _d(meta.get("dateContest") or tender.get("dateContest")) or _d(meta.get("datePublished"))
    if d is None:
        return out
    base = dict(
        tender_id=tender.get("id") or meta.get("id"),
        tender_number=meta.get("number"),
        date=d,
        region=region,
        buyer=tender.get("companyName") or meta.get("companyName"),
    )
    plan_by_product: dict[str, dict] = {}
    for lot in tender.get("lots") or []:
        for p in lot.get("products") or []:
            pd = p.get("planningDetail") or {}
            okgz = pd.get("okgz") or {}
            unit = (pd.get("measurementUnit") or {}).get("shortNameRU")
            price = p.get("price") or pd.get("priceForOneUnit")
            info = dict(
                okgz=okgz.get("codeName"),
                okgz_name=(okgz.get("name") or "").strip(),
                spec=(p.get("specifications") or pd.get("description") or "").strip() or None,
                unit=unit,
                qty=str(p.get("count") or pd.get("amount") or ""),
            )
            plan_by_product[p.get("id")] = info
            if price:
                out.append(Record(source="kg_goszakupki:plan", price=str(price), **base, **info))
    for sub in subs:
        supplier = (sub.get("companyName") or "").strip()
        for lot in sub.get("lots") or []:
            for bp in lot.get("products") or []:
                price = bp.get("priceOfUnit")
                prod = bp.get("product") or {}
                info = plan_by_product.get(prod.get("id"))
                if not price or info is None:
                    continue
                origin = (bp.get("countryOfOrigin") or {}).get("title")
                out.append(
                    Record(
                        source="kg_goszakupki:bid", price=str(price), supplier=supplier, origin=origin, **base, **info
                    )
                )
    return out


def collect(queries: list[str] | None = None, per_query: int = 40, client: Client | None = None) -> list[Record]:
    client = client or Client()
    seen: dict[str, dict] = {}
    for q in queries or QUERIES:
        first = 0
        while first < per_query:
            page = client.search(q, first=first, rows=min(50, per_query - first))
            if not page:
                break
            for m in page:
                if m.get("type") == "PRODUCT" and m.get("status") in PRICED_STATUSES:
                    seen.setdefault(m["id"], m)
            if len(page) < min(50, per_query - first):
                break
            first += len(page)
    records: list[Record] = []
    for tid, meta in seen.items():
        try:
            tender = client.tender(tid)
            subs = client.submissions(tid) if meta.get("status") != "VERIFIED_PUBLISHED" else []
        except httpx.HTTPError:
            continue
        records.extend(parse_tender(meta, tender, subs))
    return records


def is_construction(r: dict) -> bool:
    return (r.get("okgz") or "").startswith(CONSTRUCTION_OKGZ_PREFIXES)


def record_to_point(r: dict) -> MarketPoint | None:
    if not is_construction(r):
        return None
    text = " ".join(x for x in (r.get("okgz_name"), r.get("spec")) if x)
    price = Decimal(r["price"])
    if price <= 0:
        return None
    pl = PurchaseLine(
        line_id="m",
        date=date.fromisoformat(r["date"]),
        supplier=r.get("supplier") or "госзакупка",
        item_raw=text,
        qty=Decimal("1"),
        unit_raw=r.get("unit"),
        price=price,
        amount=price,
    )
    nl = normalize_line(pl)
    if nl.status != "ok" or "|u=" in (nl.sku or ""):
        return None
    return MarketPoint(
        sku=nl.sku,
        date=pl.date,
        price=nl.price,
        unit=nl.unit,
        source=r["source"],
        region=r.get("region"),
        ref=r.get("tender_number"),
        label=nl.sku_label,
    )


def records_to_points(records: list[dict]) -> tuple[list[MarketPoint], int]:
    """Записи портала → точки эталона; второе значение — сколько записей не легло на позиции."""
    points, skipped = [], 0
    for r in records:
        p = record_to_point(r)
        if p is None:
            skipped += 1
        else:
            points.append(p)
    return points, skipped
