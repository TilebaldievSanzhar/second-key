"""Сквозной сценарий API: вход → загрузка выгрузки → аудит в очереди → вопросы → два ключа."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from conftest import PASSWORD, login
from secondkey.db.models import Role
from secondkey.db.session import session_scope
from secondkey.engine import synthetic
from secondkey.services.market import points_from_raw, save_points

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


@pytest.fixture(scope="session")
def registry_xlsx(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("corpus") / "registry.xlsx"
    synthetic.write_xlsx(synthetic.Generator(11).generate(), path)
    return path


@pytest.fixture(scope="session")
async def done_run(app, org, registry_xlsx) -> dict:
    """Аудит демо-выгрузки, запущенный через API бухгалтером (задача выполняется прямо в kiq)."""
    async with session_scope() as s:
        await save_points(s, points_from_raw(synthetic.market_points()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        await login(c, "acc@t.kg")
        r = await c.post("/api/uploads", files={"file": ("Закупки.xlsx", registry_xlsx.read_bytes(), XLSX)})
        assert r.status_code == 201, r.text
        up = r.json()
        assert up["preview"]["missing"] == [] and up["preview"]["header_row"] == 5
        assert up["preview"]["data_rows"] > 1000
        r = await c.post("/api/audits", json={"upload_id": up["id"]})
        assert r.status_code == 202, r.text
        return r.json()


async def test_health(client):
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
    r = await client.get("/api/health/ready")
    assert r.status_code == 200 and r.json() == {"db": "ok", "storage": "ok"}


async def test_auth(client, org):
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.post("/api/auth/login", json={"email": "owner@t.kg", "password": "wrong-password"})
    assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"email": "OWNER@t.kg", "password": PASSWORD})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    me = (await client.get("/api/auth/me")).json()
    assert me["memberships"][0]["role"] == "owner"
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_run_completes(done_run, as_user):
    owner = await as_user("owner@t.kg")
    run = (await owner.get(f"/api/audits/{done_run['id']}")).json()
    assert run["status"] == "done", run["error"]
    assert run["progress"] == 100 and run["lines_total"] > 1000
    assert Decimal(run["overpayment"]) > 0 and run["findings_count"] > 10
    assert run["period_start"] == "2025-10-01" and run["market_points"] > 0
    assert run["created_at"] <= run["started_at"] <= run["finished_at"]

    listing = (await owner.get("/api/audits")).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == run["id"]

    # прогресс: в тестах поток отдаёт текущее состояние и закрывается
    r = await owner.get(f"/api/audits/{run['id']}/events")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"status":"done"' in r.text.replace(" ", "")


async def test_summary_has_no_double_counting(done_run, as_user):
    owner = await as_user("owner@t.kg")
    s = (await owner.get(f"/api/audits/{done_run['id']}/summary")).json()
    by_rule = sum(Decimal(r["overpayment"]) for r in s["by_rule"])
    assert abs(by_rule - Decimal(s["run"]["overpayment"])) < 1
    assert {"P-01", "P-03", "P-05"} <= {r["key"] for r in s["by_rule"]}
    assert sum(r["findings"] for r in s["by_rule"]) == s["run"]["findings_count"]
    # помесячно: суммы сходятся с итогом проверки, разбивка по правилам — без двойного счёта
    months = (await owner.get(f"/api/audits/{done_run['id']}/timeline")).json()
    assert len(months) == 12 and months[0]["month"] == "2025-10"
    assert abs(sum(Decimal(m["overpayment"]) for m in months) - Decimal(s["run"]["overpayment"])) < 1
    assert all(abs(sum(Decimal(v) for v in m["by_rule"].values()) - Decimal(m["overpayment"])) < 1 for m in months)
    # каждая строка с переплатой отнесена ровно к одному правилу
    flagged = (await owner.get(f"/api/audits/{done_run['id']}/lines", params={"flagged": True, "limit": 1})).json()
    assert sum(r["lines"] for r in s["by_rule"]) == flagged["total"]
    assert s["by_supplier"] and s["by_responsible"] and s["by_material"]
    assert s["findings"]["open"] == s["run"]["findings_count"]


async def test_two_keys(done_run, as_user):
    owner, buyer = await as_user("owner@t.kg"), await as_user("buyer@t.kg")
    all_items = (await owner.get("/api/findings", params={"run_id": done_run["id"], "limit": 200})).json()
    mine = (await buyer.get("/api/findings", params={"limit": 200})).json()
    assert 0 < mine["total"] < all_items["total"]
    assert {f["responsible"] for f in mine["items"]} == {"Асанов Б."}
    # общая картина компании и коллеги — не для снабжения
    for path in ("summary", "lines", "report"):
        assert (await buyer.get(f"/api/audits/{done_run['id']}/{path}")).status_code == 403
    money = [Decimal(f["overpayment"]) for f in all_items["items"]]
    assert money == sorted(money, reverse=True)

    f = next(f for f in mine["items"] if Decimal(f["overpayment"]) > 0)
    # ключ 1: снабжение объясняет
    r = await buyer.post(
        f"/api/findings/{f['id']}/answer",
        json={"answer": "Склад пуст, заливка плиты", "reasons": ["Срочная поставка", "Доставка включена в цену"]},
    )
    assert r.json()["answer_reasons"] == ["Срочная поставка", "Доставка включена в цену"]
    bad = await buyer.post(f"/api/findings/{f['id']}/answer", json={"answer": "текст", "reasons": ["Выдуманная"]})
    assert bad.status_code == 422
    assert r.status_code == 200 and r.json()["status"] == "answered"
    # решение — не дело снабжения
    r = await buyer.post(f"/api/findings/{f['id']}/decision", json={"status": "justified"})
    assert r.status_code == 403
    # ключ 2: собственник решает
    r = await owner.post(f"/api/findings/{f['id']}/decision", json={"status": "confirmed", "note": "Вернуть разницу"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    r = await buyer.post(f"/api/findings/{f['id']}/answer", json={"answer": "ещё раз"})
    assert r.status_code == 409

    s = (await owner.get(f"/api/audits/{done_run['id']}/summary")).json()
    assert Decimal(s["confirmed_overpayment"]) == Decimal(f["overpayment"])

    detail = (await owner.get(f"/api/findings/{f['id']}")).json()
    assert detail["answered_by"]["full_name"] == "buyer" and detail["decided_by"]["full_name"] == "owner"
    assert (
        detail["lines"]
        and abs(sum(Decimal(x["finding_overpayment"]) for x in detail["lines"]) - Decimal(f["overpayment"])) < 2
    )

    # отмена решения вместо «вы уверены?»
    r = await owner.post(f"/api/findings/{f['id']}/reopen")
    assert r.json()["status"] == "answered" and r.json()["decision_note"] is None

    hist = (await owner.get(f"/api/audits/{done_run['id']}/price-history", params={"sku": f["sku"]})).json()
    assert hist["purchases"] and any(p["flagged"] for p in hist["purchases"])


async def test_lines_and_report(done_run, as_user):
    owner = await as_user("owner@t.kg")
    flagged = (await owner.get(f"/api/audits/{done_run['id']}/lines", params={"flagged": True, "limit": 5})).json()
    assert flagged["total"] > 0 and all(Decimal(x["overpayment"]) > 0 for x in flagged["items"])
    r = await owner.get(f"/api/audits/{done_run['id']}/report")
    assert r.status_code == 200 and "Тест Курулуш" in r.text
    r = await owner.get(f"/api/audits/{done_run['id']}/report", params={"download": True})
    assert "attachment" in r.headers["content-disposition"]


async def test_permissions_and_validation(org, as_user):
    viewer, owner, acc = await as_user("viewer@t.kg"), await as_user("owner@t.kg"), await as_user("acc@t.kg")
    r = await viewer.post("/api/uploads", files={"file": ("a.xlsx", b"x", XLSX)})
    assert r.status_code == 403
    r = await acc.post("/api/uploads", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 422 and "Excel" in r.json()["detail"]

    r = await acc.patch("/api/org/settings", json={"approval_threshold": "300000"})
    assert r.status_code == 403
    r = await owner.patch("/api/org/settings", json={"approval_threshold": "300000", "market_tolerance": "0.08"})
    assert r.status_code == 200
    assert Decimal(r.json()["settings"]["approval_threshold"]) == 300000
    r = await owner.patch("/api/org/settings", json={"market_tolerance": "0.9"})
    assert r.status_code == 422
    await owner.patch("/api/org/settings", json={"approval_threshold": "500000", "market_tolerance": "0.10"})

    r = await owner.post(
        "/api/org/members",
        json={
            "email": "new@t.kg",
            "full_name": "Новый",
            "role": "buyer",
            "alias": "Новый Н.",
            "password": "long-password-1",
        },
    )
    assert r.status_code == 201
    r = await acc.post(
        "/api/org/members", json={"email": "x@t.kg", "full_name": "X", "role": "owner", "password": "long-password-1"}
    )
    assert r.status_code == 403
    members = (await owner.get("/api/org/members")).json()
    assert {m["role"] for m in members} >= {Role.owner.value, Role.buyer.value}


async def test_tenant_isolation(done_run, as_user):
    from secondkey.services.orgs import add_member, create_org

    async with session_scope() as s:
        other = await create_org(s, "Чужая компания")
        await add_member(s, other, "stranger@x.kg", "Чужой", Role.owner, PASSWORD)
    stranger = await as_user("stranger@x.kg")
    assert (await stranger.get(f"/api/audits/{done_run['id']}")).status_code == 404
    assert (await stranger.get(f"/api/audits/{done_run['id']}/events")).status_code == 404
    assert (await stranger.get("/api/findings")).json()["total"] == 0
    assert (await stranger.get("/api/audits")).json()["total"] == 0
