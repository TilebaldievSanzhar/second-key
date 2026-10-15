"""Курсы валют Национального банка КР (юрисдикция КР для аудита и стола).

Официальный XML: https://www.nbkr.kg/XML/daily.xml (курсы к сому, запятая как
десятичный разделитель, кодировка windows-1251). Снапшот хранится в БД (таблица fx_rates);
приведение цен ссылается на курс конкретной даты.
"""

from __future__ import annotations

import ssl
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal

import httpx

from secondkey.config import settings

NBKR_DAILY_URL = "https://www.nbkr.kg/XML/daily.xml"


def parse_daily(xml_bytes: bytes) -> dict:
    """XML НБКР → снапшот {'date', 'base': 'KGS', 'rates': {'USD': '87.45', …}} (сом за 1 единицу)."""
    root = ET.fromstring(xml_bytes)
    published = root.get("Date")
    rates: dict[str, str] = {}
    for cur in root.iter("Currency"):
        code = cur.get("ISOCode")
        value = (cur.findtext("Value") or "").replace(",", ".").strip()
        nominal = (cur.findtext("Nominal") or "1").strip()
        if code and value:
            rates[code] = str(Decimal(value) / Decimal(nominal or "1"))
    pub = datetime.strptime(published, "%d.%m.%Y").date().isoformat() if published else date.today().isoformat()
    return {"source": "nbkr.kg", "published": pub, "base": "KGS", "rates": rates}


def fetch_rates() -> dict:
    resp = httpx.get(
        NBKR_DAILY_URL,
        timeout=60,
        headers={"User-Agent": settings.http_user_agent},
        verify=ssl.create_default_context(),
    )
    resp.raise_for_status()
    snap = parse_daily(resp.content)
    snap["fetched_on"] = date.today().isoformat()
    return snap


def to_kgs(amount: Decimal, currency: str, snapshot: dict) -> Decimal:
    if currency == "KGS":
        return amount
    rate = snapshot["rates"].get(currency)
    if rate is None:
        raise KeyError(f"нет курса {currency} в снапшоте НБКР {snapshot.get('published')}")
    return amount * Decimal(rate)
