"""Связки снабженца и поставщика: ИНН, сигналы L-xx, триангуляция, бенчмарк, API (кто что видит)."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from conftest import login
from secondkey.engine import bench, synthetic
from secondkey.engine.importer import import_registry
from secondkey.engine.inn import clean_inn, inn_hash, parse_inn
from secondkey.engine.links import run_links
from secondkey.engine.models import AuditFinding, NormLine, PurchaseLine, StaffRef, SupplierCard
from secondkey.engine.pipeline import combine_overpayment

D = Decimal

# --- ИНН ----------------------------------------------------------------------------------------


def test_inn_kind_and_date():
    org = parse_inn("01011202510327")
    assert org.kind == "org" and org.date == date(2025, 11, 10)
    ip = parse_inn("20304200410556")
    assert (ip.kind, ip.gender, ip.date) == ("person", "m", date(2004, 4, 3))
    assert parse_inn("11407198800452").gender == "f"
    assert parse_inn("40101199000123").kind == "person"  # иностранец: 3/4 — по порядку присвоения ПИН с 2025 г.
    assert parse_inn("01399202510327").date is None  # 13.99 — дата в номере не читается
    assert parse_inn("91011202510327") is None and parse_inn("123") is None


def test_inn_cleanup_from_excel():
    assert clean_inn(1204201810015) == "01204201810015"  # Excel съел ведущий ноль юрлица
    assert clean_inn(20304200410556.0) == "20304200410556"
    assert clean_inn("0120 4201 8100 15") == "01204201810015"
    assert inn_hash("s", "123") == inn_hash("s", "123") != inn_hash("t", "123")


# --- сигналы на маленьких примерах --------------------------------------------------------------


def _n(i, d, supplier, who, price, sku="rebar12", qty="5", inn=None, doc_in=None) -> NormLine:
    amount = D(price) * D(qty)
    line = PurchaseLine(
        line_id=f"r{i}",
        date=d,
        supplier=supplier,
        item_raw=sku,
        qty=D(qty),
        unit_raw="т",
        price=D(price),
        amount=amount,
        supplier_inn=inn,
        responsible=who,
        doc_in_no=doc_in,
    )
    return NormLine(line, sku, sku.capitalize(), sku, "t", D(qty), D(price))


def _world(premium: str = "1.15", second_buyer: bool = True) -> list[NormLine]:
    """Полгода арматуры: Асанов покупает и у «своего» ИП, Токтосунов — у двух оптовиков."""
    start = date(2025, 10, 1)
    rows, i = [], 0
    for w in range(26):
        d = start + timedelta(days=7 * w)
        i += 1
        rows.append(_n(i, d, "ОсОО «Темир Снаб»", "Асанов Б.", "70000"))
        if second_buyer:
            i += 1
            rows.append(_n(i, d + timedelta(days=1), "ОсОО «Металл Базар»", "Токтосунов Э.", "70500"))
        if w >= 13 and w % 2 == 0:
            i += 1
            rows.append(
                _n(
                    i,
                    d + timedelta(days=2),
                    "ИП Сыдыков",
                    "Асанов Б.",
                    str(int(70000 * float(premium))),
                    inn="20304200410556",
                    doc_in=str(40 + w // 2),
                )
            )
    return rows


def _codes(traces) -> set[str]:
    return {t.code for t in traces}


def test_pair_premium_exclusivity_and_identity_make_a_link():
    lines = _world()
    found, profiles = run_links(lines, [])
    sy = next(p for p in profiles if p.name == "ИП Сыдыков")
    assert {"L-01", "L-04", "L-09", "L-10"} <= _codes(sy.traces)
    link = next(f for f in found if f.supplier == "ИП Сыдыков")
    assert link.code == "LINK" and link.evidence["responsible"] == "Асанов Б."
    assert link.evidence["groups"] == ["price", "behavior", "identity"] and link.severity == "high"
    assert link.line_overpayment == {}  # деньги связка не добавляет
    assert "Токтосунов Э." in link.message and "ИНН" not in link.message  # снабженцу — только о покупках


def test_fixed_categories_do_not_look_like_exclusivity():
    """Позицию покупает один человек — сравнивать не с кем: ни премии пары, ни эксклюзивности."""
    found, profiles = run_links(_world(second_buyer=False), [])
    sy = next(p for p in profiles if p.name == "ИП Сыдыков")
    assert not ({"L-01", "L-04"} & _codes(sy.traces))
    assert {"L-09", "L-10"} <= _codes(sy.traces)  # остались только следы личности — одна группа
    assert not any(f.supplier == "ИП Сыдыков" for f in found)


def test_single_identity_trace_is_not_a_question_but_staff_match_is():
    start = date(2026, 1, 5)
    lines = [
        _n(i, start + timedelta(days=10 * i), "ИП Бекова С.", "Токтосунов Э.", "19500", "board", inn="11407198800452")
        for i in range(5)
    ]
    lines += [
        _n(10 + i, start + timedelta(days=10 * i), "ОсОО «Лесторг»", "Токтосунов Э.", "19400", "board")
        for i in range(5)
    ]
    found, _ = run_links(lines, [])
    assert found == []
    staff = [StaffRef(name="Бекова С.", role="прораб", inn_hash=inn_hash("salt", "11407198800452"))]
    found, _ = run_links(lines, [], staff=staff, hash_inn=lambda inn: inn_hash("salt", inn))
    assert [f.supplier for f in found] == ["ИП Бекова С."]
    assert found[0].evidence["traces"][0]["code"] == "L-12" and found[0].severity == "high"


def test_shared_requisites_but_not_same_business_center():
    lines = _world()
    cards = {
        "ОсОО «Темир Снаб»": SupplierCard(
            "ОсОО «Темир Снаб»", phone="+996 555 41-27-09", address="Бишкек, БЦ Орион, офис 1"
        ),
        "ОсОО «Металл Базар»": SupplierCard(
            "ОсОО «Металл Базар»", phone="0555412709", address="Бишкек, БЦ Орион, офис 1"
        ),
        "ИП Сыдыков": SupplierCard("ИП Сыдыков", address="Бишкек, БЦ Орион, офис 1"),
    }
    _, profiles = run_links(lines, [], cards=cards)
    by = {p.name: _codes(p.traces) for p in profiles}
    assert "L-11" in by["ОсОО «Темир Снаб»"] and "L-11" in by["ОсОО «Металл Базар»"]  # один телефон
    assert "L-11" not in by["ИП Сыдыков"]  # совпал только адрес — бизнес-центр


def test_price_findings_become_price_trace_without_double_money():
    lines = _world(premium="1.00")  # без премии: цена у ИП обычная
    over = {n.line_id: D("60000") for n in lines if n.line.supplier == "ИП Сыдыков"}
    p01 = AuditFinding(
        "P-01",
        "high",
        "t",
        "Арматура: у ИП дороже",
        list(over),
        D("420000"),
        supplier="ИП Сыдыков",
        line_overpayment=over,
    )
    found, _ = run_links(lines, [p01])
    link = next(f for f in found if f.supplier == "ИП Сыдыков")
    assert "P-01" in {t["code"] for t in link.evidence["traces"]}
    assert link.overpayment == D("360000")  # справочно: сумма вкладов строк пары
    assert combine_overpayment([p01, *found]) == combine_overpayment([p01])  # итог прогона не меняется


# --- синтетика и бенчмарк -----------------------------------------------------------------------


@pytest.mark.parametrize("seed", [11, 23])
def test_bench_finds_planted_links(seed):
    with tempfile.TemporaryDirectory() as d:
        synthetic.build(Path(d), seed=seed)
        metrics, _, _ = bench.run(Path(d))
    links = metrics["links"]
    assert links["recall"] == 1.0, links
    assert links["trace_recall"] >= 0.9, links
    assert len(links["false_links"]) <= 1, links
    assert metrics["money"]["line_precision"] >= 0.95 and metrics["money"]["ratio_found_to_true"] >= 0.8


def test_excel_columns_for_links(tmp_path: Path):
    path = tmp_path / "registry.xlsx"
    synthetic.write_xlsx(synthetic.Generator(5).generate()[:40], path)
    imp = import_registry(path)
    assert imp.mapping["doc_in_no"] != imp.mapping["doc_no"]  # «Вх. номер» не забирает «Документ»
    first = imp.lines[0]
    assert first.doc_no.startswith("Поступление") and first.doc_in_no and first.doc_in_date
    assert all(c.phone and c.bank_account for c in imp.suppliers.values())
    assert any(c.inn for c in imp.suppliers.values())


# --- API: выгрузка из 1С с карточками и сотрудниками; кто что видит -------------------------------


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


async def test_links_via_1c_push(org, as_user, app):
    acc = await as_user("acc@t.kg")
    tok = (await acc.post("/api/integrations/tokens", json={"name": "1С", "scopes": ["push"]})).json()["token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        h = {"Authorization": f"Bearer {tok}"}
        salt = (await raw.get("/api/integrations/ping", headers=h)).json()["inn_salt"]
        assert salt == (await raw.get("/api/integrations/ping", headers=h)).json()["inn_salt"]  # соль не меняется
        gen = synthetic.Generator(11)
        body = {
            "source": "1c:bp30",
            "lines": synthetic.records(gen.generate()),
            "suppliers": synthetic.supplier_cards(),
            "staff": synthetic.staff(salt),
        }
        r = await raw.post("/api/integrations/purchases", headers=h, json=body)
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]

    owner = await as_user("owner@t.kg")
    run = (await owner.get(f"/api/audits/{run_id}")).json()
    assert run["status"] == "done", run["error"]
    links = (await owner.get("/api/findings", params={"run_id": run_id, "code": "LINK", "limit": 50})).json()["items"]
    by_supplier = {f["supplier"]: f for f in links}
    assert {"ИП Сыдыков А.Т.", "ИП Бекова С.", synthetic.B_SUPPLIER} <= set(by_supplier)

    sy = by_supplier["ИП Сыдыков А.Т."]
    full = (await owner.get(f"/api/findings/{sy['id']}")).json()
    assert {"price", "behavior", "identity"} == set(full["evidence"]["groups"])
    assert all(Decimal(ln["finding_overpayment"]) == 0 for ln in full["lines"])
    assert full["lines"][0]["doc_in_no"]

    buyer = await as_user("buyer@t.kg")  # в учёте — «Асанов Б.»
    seen = (await buyer.get(f"/api/findings/{sy['id']}")).json()
    assert {t["group"] for t in seen["evidence"]["traces"]} == {"price"}  # без досье на себя
    assert (await buyer.get(f"/api/findings/{by_supplier['ИП Бекова С.']['id']}")).status_code == 404  # не его

    summary = (await owner.get(f"/api/audits/{run_id}/summary")).json()
    link_row = next(r for r in summary["by_rule"] if r["key"] == "LINK")
    assert Decimal(link_row["overpayment"]) == 0 and link_row["findings"] == len(links)

    suppliers = (await owner.get(f"/api/audits/{run_id}/suppliers", params={"linked": True})).json()
    bekova = next(s for s in suppliers if s["name"] == "ИП Бекова С.")
    assert bekova["inn_kind"] == "person" and any(t["code"] == "L-12" for t in bekova["traces"])
    assert bekova["card"]["phone"] and bekova["buyers"][0]["name"] == "Токтосунов Э."
    for email in ("buyer@t.kg", "acc@t.kg", "viewer@t.kg"):
        c = await as_user(email)
        assert (await c.get(f"/api/audits/{run_id}/suppliers")).status_code == 403
