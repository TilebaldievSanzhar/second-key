"""Неделя 3 связок: оплаты (аванс без поставки, предоплата, оплата вне очереди), реестр (ручная проверка и
агрегатор), налоговый риск, общий руководитель, пересчёт связок без потери ответов, обезличенная сеть."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from conftest import login
from secondkey.engine import synthetic
from secondkey.engine.importer import import_registry
from secondkey.engine.links import run_links
from secondkey.engine.models import NormLine, PurchaseLine
from secondkey.engine.payments import Payment, advance_findings, payment_traces, profiles
from secondkey.engine.registry import RegistryRecord, person_key
from secondkey.engine.rules import AuditConfig
from secondkey.registries import RegistryError
from secondkey.registries.statsnet import StatsnetProvider

D = Decimal
CFG = AuditConfig()


def _n(i, d, supplier, who="Асанов Б.", amount="100000", inn=None, sku="rebar12") -> NormLine:
    line = PurchaseLine(
        line_id=f"r{i}",
        date=d,
        supplier=supplier,
        item_raw=sku,
        qty=D("1"),
        unit_raw="т",
        price=D(amount),
        amount=D(amount),
        supplier_inn=inn,
        responsible=who,
    )
    return NormLine(line, sku, sku.capitalize(), sku, "t", D("1"), D(amount))


# --- оплаты ---------------------------------------------------------------------------------------


def test_fifo_prepay_delay_and_advance():
    start = date(2026, 1, 10)
    lines = [_n(i, start + timedelta(days=30 * i), "ОсОО «Бетон»", amount="1000000") for i in range(4)]
    pays = [Payment(start + timedelta(days=30 * i - 3), "ОсОО «Бетон»", D("1000000")) for i in range(4)]
    pays.append(Payment(start + timedelta(days=100), "ОсОО «Бетон»", D("600000")))  # аванс без поставки
    end = start + timedelta(days=150)
    p = profiles(lines, pays, CFG, end)["ОсОО «Бетон»"]
    assert p.prepay_share == 1 and p.advance == D("600000") and p.advance_since == start + timedelta(days=100)
    [f] = advance_findings({"ОсОО «Бетон»": p}, lines, CFG, end)
    assert f.code == "P-09" and f.evidence["days"] == 50 and f.overpayment == 0
    assert f.evidence["responsible"] == "Асанов Б." and f.line_ids == []


def test_fresh_advance_and_short_prepay_are_not_questions():
    start = date(2026, 1, 10)
    lines = [_n(i, start + timedelta(days=30 * i), "ОсОО «Бетон»", amount="1000000") for i in range(4)]
    pays = [Payment(start + timedelta(days=30 * i - 3), "ОсОО «Бетон»", D("1000000")) for i in range(4)]
    end = start + timedelta(days=90)  # предоплата за 3 дня до поставки — не «аванс без поставки»
    p = profiles(lines, pays, CFG, end)["ОсОО «Бетон»"]
    assert p.advance == 0 and advance_findings({"ОсОО «Бетон»": p}, lines, CFG, end) == []


def test_prepay_and_fast_payment_traces_against_others():
    start = date(2026, 1, 5)
    lines, pays, i = [], [], 0
    for w in range(10):
        d = start + timedelta(days=7 * w)
        for sup in ("ОсОО «Темир»", "ОсОО «Металл»"):
            i += 1
            lines.append(_n(i, d, sup))
            pays.append(Payment(d + timedelta(days=30), sup, D("100000")))  # остальным — через месяц
        i += 1
        lines.append(_n(i, d, "ИП Быстров"))
        pays.append(Payment(d, "ИП Быстров", D("100000")))  # в день поставки
        i += 1
        lines.append(_n(i, d, "ОсОО «Вперёд»"))
        pays.append(Payment(d - timedelta(days=2), "ОсОО «Вперёд»", D("100000")))  # заранее
    end = start + timedelta(days=120)
    traces = payment_traces(profiles(lines, pays, CFG, end), CFG, end)
    kinds = {t.supplier: t.facts["kind"] for t in traces}
    assert kinds == {"ИП Быстров": "fast", "ОсОО «Вперёд»": "prepay"}
    assert all(t.code == "L-07" and t.group == "behavior" for t in traces)


def test_payments_sheet_in_excel(tmp_path):
    lines = synthetic.Generator(5).generate()
    path = tmp_path / "registry.xlsx"
    synthetic.write_xlsx(lines, path, synthetic.payments(lines))
    imp = import_registry(path)
    assert len(imp.payments) == len(synthetic.payments(lines)) > 50
    assert imp.payments[0].supplier_inn and imp.payments[0].purpose


# --- реестр ---------------------------------------------------------------------------------------


def test_registry_record_roundtrip_and_person_key():
    rec = RegistryRecord(inn="01011202510327", source="manual", status="Ликвидировано", founders=["Иванов П.С."])
    again = RegistryRecord.from_dict(rec.as_dict())
    assert again == rec and again.inactive
    assert person_key("Сыдыков Азамат Талантбекович") == person_key("СЫДЫКОВ А.Т.") == "сыдыков а т"
    assert person_key("Сыдыков") is None


def test_registry_signals_tax_risk_shared_director_and_registration_date():
    start = date(2026, 1, 5)
    lines = [_n(i, start + timedelta(days=10 * i), "ОсОО «Альфа»", inn="01510201910001") for i in range(4)]
    lines += [_n(10 + i, start + timedelta(days=10 * i), "ОсОО «Бета»", inn="01510201910002") for i in range(4)]
    lines += [_n(20 + i, start + timedelta(days=10 * i), "ОсОО «Гамма»", inn="01510201910003") for i in range(4)]
    registry = {
        "01510201910001": RegistryRecord("01510201910001", "manual", status="Ликвидировано", director="Осмонов Т.К."),
        "01510201910002": RegistryRecord("01510201910002", "statsnet", director="Осмонов Талант Кубанычбекович"),
        "01510201910003": RegistryRecord("01510201910003", "manual", registered=start - timedelta(days=60)),
    }
    _, profs = run_links(lines, [], CFG, registry=registry)
    by = {p.name: {t.code: t for t in p.traces} for p in profs}
    assert "L-13" in by["ОсОО «Альфа»"] and by["ОсОО «Альфа»"]["L-13"].money > 0
    assert "Ликвидировано" in by["ОсОО «Альфа»"]["L-13"].text and "НДС" in by["ОсОО «Альфа»"]["L-13"].text
    assert "L-11" in by["ОсОО «Альфа»"] and "L-11" in by["ОсОО «Бета»"]  # общий руководитель
    assert "Осмонов" in by["ОсОО «Бета»"]["L-11"].text
    assert by["ОсОО «Гамма»"]["L-10"].facts["by_registry"] is True  # дата по реестру, не по номеру ИНН
    assert "по реестру" in by["ОсОО «Гамма»"]["L-10"].text


def test_network_trace_needs_cheaper_price_at_others():
    start = date(2026, 1, 5)
    lines = [_n(i, start + timedelta(days=7 * i), "ИП Сыдыков", inn="20304200410556", amount="80000") for i in range(5)]
    net = {("20304200410556", "rebar12"): {"price": D("70000"), "orgs": 3, "points": 24}}
    _, profs = run_links(lines, [], CFG, network=net)
    [t] = [t for t in profs[0].traces if t.code == "L-03"]
    assert t.group == "price" and "3 компаний" in t.text and t.money == D("50000")
    _, profs = run_links(
        lines, [], CFG, network={("20304200410556", "rebar12"): {"price": D("79000"), "orgs": 3, "points": 24}}
    )
    assert not any(t.code == "L-03" for t in profs[0].traces)  # разница в пределах допуска


# --- Statsnet по форме ответов из документации -----------------------------------------------------

COMPANY = {
    "company": {
        "id": 777,
        "title": "ОсОО «Бетон Плюс Строй»",
        "identifiers": [{"type": "inn", "value": "01011202510327"}],
        "incorporation_date": "2025-11-10T00:00:00Z",
        "status": "Active",
        "inactive": False,
        "officers": [{"name": "Мамытова Айгуль", "position": "Директор"}],
        "addresses": [{"address": "г. Бишкек, ул. Складская, 30"}],
    },
    "risks": {"company": [{"title": "Массовый адрес регистрации"}, {"title": "Реестр недобросовестных поставщиков"}]},
    "relations": [{"connection": "shareholders", "count": 1}],
}


def statsnet_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer sk_live_test"
    if request.url.path.endswith("/companies/search"):
        inn = request.read().decode()
        if "01011202510327" in inn:
            return httpx.Response(
                200, json={"companies": [{"id": 777, "identifiers": [{"type": "inn", "value": "01011202510327"}]}]}
            )
        return httpx.Response(200, json={"companies": []})
    if request.url.path.endswith("/companies/777"):
        return httpx.Response(200, json=COMPANY)
    if request.url.path.endswith("/companies/777/relations"):
        return httpx.Response(200, json=[{"connection": "shareholders", "items": [{"name": "Мамытов Руслан"}]}])
    return httpx.Response(404, json={"status": 404, "message": "not found"})


async def test_statsnet_maps_company_and_relations():
    prov = StatsnetProvider(
        "sk_live_test", "https://statsnet.test/api/v1", transport=httpx.MockTransport(statsnet_handler)
    )
    rec = await prov.lookup("01011202510327")
    assert rec.name == "ОсОО «Бетон Плюс Строй»" and rec.registered == date(2025, 11, 10) and rec.active is True
    assert rec.director == "Мамытова Айгуль" and rec.founders == ["Мамытов Руслан"]
    assert (
        rec.unreliable is True and rec.risks == ["Массовый адрес регистрации"] and rec.address.startswith("г. Бишкек")
    )
    assert await prov.lookup("01999201910001") is None
    await prov.aclose()
    bad = StatsnetProvider(
        "sk_bad", "https://statsnet.test/api/v1", transport=httpx.MockTransport(lambda r: httpx.Response(401))
    )
    with pytest.raises(RegistryError):
        await bad.lookup("01011202510327")
    await bad.aclose()


# --- API: выгрузка с оплатами, ручная проверка по реестру, пересчёт связок ---------------------------


@pytest.fixture
async def as_user(app):
    clients: list[httpx.AsyncClient] = []

    async def make(email: str) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return await login(c, email)

    yield make
    for c in clients:
        await c.aclose()


async def _push(app, as_user, lines=None, pays=True) -> str:
    acc = await as_user("acc@t.kg")
    tok = (await acc.post("/api/integrations/tokens", json={"name": "1С", "scopes": ["push"]})).json()["token"]
    lines = lines or synthetic.Generator(11).generate()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        h = {"Authorization": f"Bearer {tok}"}
        salt = (await raw.get("/api/integrations/ping", headers=h)).json()["inn_salt"]
        body = {
            "source": "1c:bp30",
            "lines": synthetic.records(lines),
            "suppliers": synthetic.supplier_cards(),
            "staff": synthetic.staff(salt),
            "payments": synthetic.payments(lines) if pays else [],
        }
        r = await raw.post("/api/integrations/purchases", headers=h, json=body)
        assert r.status_code == 202, r.text
        return r.json()["run_id"]


async def test_advance_question_and_supplier_payments(org, as_user, app):
    run_id = await _push(app, as_user)
    owner = await as_user("owner@t.kg")
    p09 = (await owner.get("/api/findings", params={"run_id": run_id, "code": "P-09"})).json()["items"]
    assert [f["supplier"] for f in p09] == [synthetic.B_SUPPLIER] and p09[0]["responsible"] == synthetic.NEW_BUYER
    detail = (await owner.get(f"/api/findings/{p09[0]['id']}")).json()
    ev = detail["evidence"]
    assert Decimal(ev["advance"]) == synthetic.ADVANCE and ev["since_payments"][0]["amount"] == str(synthetic.ADVANCE)
    assert Decimal(ev["since_paid"]) > Decimal(ev["since_delivered"])  # с этой даты оплачено больше, чем поставлено
    buyer = await as_user("buyer@t.kg")  # Асанов: аванс по бетону Мамытова ему не виден
    assert (await buyer.get(f"/api/findings/{p09[0]['id']}")).status_code == 404

    ov = (await owner.get(f"/api/audits/{run_id}/links")).json()
    beton = next(s for s in ov["suppliers"] if s["name"] == synthetic.B_SUPPLIER)
    card = (await owner.get(f"/api/audits/{run_id}/suppliers/{beton['id']}")).json()
    assert Decimal(card["payment"]["prepay_share"]) == 1 and Decimal(card["payment"]["advance"]) == synthetic.ADVANCE
    assert card["payments_list"] and {t["code"] for t in card["traces"]} >= {"L-07"}
    caps = (await owner.get("/api/integrations/capabilities")).json()
    assert caps["registry"] == ""
    assert (await owner.post(f"/api/audits/{run_id}/registry/fetch")).status_code == 409  # агрегатор не подключён


async def test_manual_registry_refreshes_links_and_keeps_answers(org, as_user, app):
    run_id = await _push(app, as_user, pays=False)
    owner = await as_user("owner@t.kg")
    buyer = await as_user("buyer@t.kg")
    ov = (await owner.get(f"/api/audits/{run_id}/links")).json()
    sy = next(link for link in ov["links"] if link["supplier"] == "ИП Сыдыков А.Т.")
    r = await buyer.post(
        f"/api/findings/{sy['finding_id']}/answer", json={"answer": "Срочная поставка, у Темир Снаба не было"}
    )
    assert r.status_code == 200

    # ручная проверка по выписке: ИП прекратил деятельность
    r = await owner.put(
        f"/api/audits/{run_id}/suppliers/{sy['supplier_id']}/registry",
        json={
            "status": "Прекращена деятельность ИП",
            "active": False,
            "registered": "2023-06-01",
            "url": "https://record.minjust.gov.kg",
        },
    )
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["registry"]["source"] == "manual" and card["registry"]["status"].startswith("Прекращена")
    assert any(t["code"] == "L-13" for t in card["traces"])
    after = (await owner.get(f"/api/findings/{sy['finding_id']}")).json()  # тот же вопрос, ответ на месте
    assert after["status"] == "answered" and after["answer"].startswith("Срочная")
    assert "L-13" in {t["code"] for t in after["evidence"]["traces"]}

    # общий руководитель у двух поставщиков без других следов — след есть, вопроса нет
    sups = {s["name"]: s for s in ov["suppliers"]}
    for name in ("ОсОО «Лесторг»", "ОсОО «Изоляция КГ»"):
        r = await owner.put(
            f"/api/audits/{run_id}/suppliers/{sups[name]['id']}/registry",
            json={"director": "Осмонов Талант Кубанычбекович"},
        )
        assert r.status_code == 200
    ov2 = (await owner.get(f"/api/audits/{run_id}/links")).json()
    lestorg = next(s for s in ov2["suppliers"] if s["name"] == "ОсОО «Лесторг»")
    assert any(t["code"] == "L-11" and "Осмонов" in t["text"] for t in lestorg["traces"])
    assert len(ov2["links"]) == len(ov["links"])  # одна группа следов — не вопрос
    acc = await as_user("acc@t.kg")
    assert (await acc.put(f"/api/audits/{run_id}/suppliers/{sy['supplier_id']}/registry", json={})).status_code == 403


async def test_network_opt_in_and_double_price(org, as_user, app):
    from secondkey.db.models import Organization
    from secondkey.db.session import session_scope
    from secondkey.integrations.contract import PurchasesIn
    from secondkey.services.audits import execute_run
    from secondkey.services.integrations import submit_purchases
    from secondkey.services.orgs import create_org

    owner = await as_user("owner@t.kg")
    assert (await owner.patch("/api/org/network", json={"opt_in": True})).json()["network_opt_in"] is True

    async def other_org(name: str, seed: int) -> None:
        lines = synthetic.Generator(seed).generate()
        recs = synthetic.records(lines)
        for r in recs:  # у других клиентов ИП Сыдыков продаёт ту же арматуру на 15% дешевле
            if r["supplier"] == "ИП Сыдыков А.Т.":
                r["price"] = str((Decimal(r["price"]) * Decimal("0.85")).quantize(Decimal("0.01")))
                r["amount"] = str((Decimal(r["price"]) * Decimal(r["qty"])).quantize(Decimal("0.01")))
        async with session_scope() as s:
            o = await create_org(s, name, {"network_opt_in": True})
            run = await submit_purchases(s, o.id, PurchasesIn(source="test", lines=recs))
            run_id = run.id
        await execute_run(run_id)

    await other_org("ОсОО «Сеть 1»", 21)
    await other_org("ОсОО «Сеть 2»", 22)
    run_id = await _push(app, as_user, pays=False)
    ov = (await owner.get(f"/api/audits/{run_id}/links")).json()
    sy = next(s for s in ov["suppliers"] if s["name"] == "ИП Сыдыков А.Т.")
    assert not any(t["code"] == "L-03" for t in sy["traces"])  # две других компании — сеть молчит

    await other_org("ОсОО «Сеть 3»", 23)
    run_id = await _push(app, as_user, pays=False)
    ov = (await owner.get(f"/api/audits/{run_id}/links")).json()
    sy = next(s for s in ov["suppliers"] if s["name"] == "ИП Сыдыков А.Т.")
    l03 = next((t for t in sy["traces"] if t["code"] == "L-03"), None)
    assert l03 and "обезличенно: 3 компаний" in l03["text"], [t["code"] for t in sy["traces"]]
    async with session_scope() as s:  # согласие снято — сеть не видна
        from sqlalchemy import select

        o = await s.scalar(select(Organization).where(Organization.name == "ОсОО «Тест Курулуш»"))
        o.settings = {**(o.settings or {}), "network_opt_in": False}
