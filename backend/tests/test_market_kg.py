"""Рынок КР: разбор ответа портала госзакупок, привязка к позициям, курсы НБКР, НДС КР."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from secondkey.engine.market import MarketPoint, MarketStore
from secondkey.market.kg_goszakupki import parse_tender, record_to_point
from secondkey.refdata.fx_kg import parse_daily, to_kgs
from secondkey.refdata.vat import vat_rate, vat_rate_kg

FIX = Path(__file__).parent / "fixtures" / "kg_goszakupki_cement.json"


def test_parse_real_cement_tender():
    fx = json.loads(FIX.read_text(encoding="utf-8"))
    recs = parse_tender(fx["meta"], fx["tender"], fx["submissions"])
    plan = [r for r in recs if r.source.endswith("plan")]
    bids = sorted(Decimal(r.price) for r in recs if r.source.endswith("bid"))
    assert len(plan) == 1 and plan[0].price == "8500" and plan[0].unit == "т"
    assert plan[0].okgz == "44111200-3" and plan[0].date == "2026-10-12" and plan[0].region == "Сузакский район"
    assert bids == [Decimal("7990"), Decimal("8200")]

    point = record_to_point(plan[0].__dict__)
    assert point is not None
    assert point.sku == "цемент|m=М400" and point.unit == "t" and point.price == Decimal("8500.00")


def test_market_store_window_lookup(tmp_path):
    pts = [
        MarketPoint("песок|kind=мытый", date(2026, 3, d), Decimal(p), "m3", "test")
        for d, p in ((1, "1100"), (15, "1150"), (28, "1200"))
    ]
    store = MarketStore(pts)
    assert store.lookup("песок|kind=мытый", date(2026, 3, 16), 5) == [Decimal("1150")]
    assert len(store.lookup("песок|kind=мытый", date(2026, 3, 16), 30)) == 3
    path = tmp_path / "p.jsonl"
    store.save_jsonl(path)
    assert len(MarketStore.load_jsonl(path)) == 3


def test_nbkr_daily_xml_and_vat():
    xml = """<?xml version="1.0" encoding="windows-1251" ?>
<CurrencyRates Name="Daily Exchange Rates" Date="10.10.2026">
<Currency ISOCode="USD"><Nominal>1</Nominal><Value>87,4500</Value></Currency>
<Currency ISOCode="KZT"><Nominal>1</Nominal><Value>0,1927</Value></Currency>
</CurrencyRates>""".encode("cp1251")
    snap = parse_daily(xml)
    assert snap["published"] == "2026-10-10" and snap["rates"]["USD"] == "87.4500"
    assert to_kgs(Decimal("100"), "USD", snap) == Decimal("8745.0000")
    assert vat_rate_kg(date(2026, 1, 1)) == Decimal("0.12")
    assert vat_rate("KZ", date(2026, 2, 1)) == Decimal("0.16")
