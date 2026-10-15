"""Интеграции: токены, приём строк из 1С по API, сопоставление колонок «как в прошлый раз», OData, MCP."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import login
from secondkey.engine import synthetic
from secondkey.integrations.onec_odata import PROFILES, OneCOData
from secondkey.services import integrations as svc

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def synthetic_lines() -> list[dict]:
    lines = synthetic.Generator(23).generate()
    return [
        {
            "date": ln.d.isoformat(),
            "doc": f"Поступление {ln.doc} от {ln.d:%d.%m.%Y}",
            "supplier": ln.supplier,
            "supplier_inn": synthetic.SUPPLIERS[ln.supplier][1],
            "item": ln.name,
            "unit": ln.unit,
            "qty": str(ln.qty),
            "price": str(ln.price),
            "amount": str(ln.amount),
            "object": ln.obj,
            "responsible": ln.who,
        }
        for ln in lines
    ]


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


@pytest.fixture
async def token(org, as_user):
    acc = await as_user("acc@t.kg")
    r = await acc.post("/api/integrations/tokens", json={"name": "1С бухгалтерия", "scopes": ["push", "mcp"]})
    assert r.status_code == 201, r.text
    return r.json()


async def test_token_lifecycle_and_permissions(org, as_user, app):
    viewer = await as_user("viewer@t.kg")
    assert (await viewer.post("/api/integrations/tokens", json={"name": "x", "scopes": ["push"]})).status_code == 403
    acc = await as_user("acc@t.kg")
    created = (await acc.post("/api/integrations/tokens", json={"name": "только MCP", "scopes": ["mcp"]})).json()
    assert created["token"].startswith("sk_") and created["prefix"] == created["token"][:10]
    listed = (await acc.get("/api/integrations/tokens")).json()
    assert all("token" not in t for t in listed)  # токен показывается один раз

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        h = {"Authorization": f"Bearer {created['token']}"}
        assert (await raw.get("/api/integrations/ping", headers=h)).status_code == 401  # нет права push
        await acc.delete(f"/api/integrations/tokens/{created['id']}")
        assert (
            await raw.get("/api/integrations/ping", headers={"Authorization": "Bearer sk_wrong"})
        ).status_code == 401


async def test_capabilities(org, as_user):
    acc = await as_user("acc@t.kg")
    caps = (await acc.get("/api/integrations/capabilities")).json()
    assert caps["secrets"] is True and caps["llm"] is False
    buyer = await as_user("buyer@t.kg")
    assert (await buyer.get("/api/integrations/capabilities")).status_code == 403


async def test_push_from_1c_runs_audit(token, as_user, app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        h = {"Authorization": f"Bearer {token['token']}"}
        assert (await raw.get("/api/integrations/ping", headers=h)).json()["status"] == "ok"
        bad = await raw.post(
            "/api/integrations/purchases",
            headers=h,
            json={"lines": [{"date": "2026-01-01", "supplier": "А", "item": "Цемент", "qty": "1"}]},
        )
        assert bad.status_code == 422  # нет ни цены, ни суммы
        r = await raw.post(
            "/api/integrations/purchases", headers=h, json={"source": "1c:bp30", "lines": synthetic_lines()}
        )
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
    owner = await as_user("owner@t.kg")
    run = (await owner.get(f"/api/audits/{run_id}")).json()
    assert run["status"] == "done", run["error"]
    assert run["lines_total"] > 1000 and run["findings_count"] > 10
    assert run["filename"].startswith("1c_bp30")
    lines = (await owner.get(f"/api/audits/{run_id}/lines", params={"limit": 1})).json()
    assert lines["items"][0]["source_row"] == 1  # номер строки — номер записи в выгрузке


async def test_mapping_is_remembered_for_same_report(org, as_user, tmp_path: Path):
    path = tmp_path / "registry.xlsx"
    synthetic.write_xlsx(synthetic.Generator(5).generate()[:50], path)
    acc = await as_user("acc@t.kg")
    first = (await acc.post("/api/uploads", files={"file": ("a.xlsx", path.read_bytes(), XLSX)})).json()
    assert first["preview"]["suggested"] is None
    mapping = {k: v for k, v in first["preview"]["mapping"].items() if k != "vat"}  # бухгалтер убрал колонку НДС
    r = await acc.post("/api/audits", json={"upload_id": first["id"], "mapping": mapping, "header_row": 5})
    assert r.status_code == 202
    again = (await acc.post("/api/uploads", files={"file": ("b.xlsx", path.read_bytes(), XLSX)})).json()
    assert again["preview"]["suggested"] == {"header_row": 5, "mapping": mapping, "by": "saved", "notes": ""}


# --- 1С по OData на подставных ответах ------------------------------------------------------------

DOC_KEY, SUP_KEY, ITEM_KEY, UNIT_KEY, WH_KEY, USER_KEY = (
    f"0000000{i}-0000-0000-0000-000000000000" for i in range(1, 7)
)


def odata_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    assert request.headers["authorization"].startswith("Basic ")
    if path.endswith("$metadata"):
        return httpx.Response(
            200,
            text='<EntityType Name="Document_ПоступлениеТоваровУслуг">',
            headers={"content-type": "application/xml"},
        )
    if "Document_ПоступлениеТоваровУслуг" in path:
        doc = {
            "Ref_Key": DOC_KEY,
            "Date": "2026-01-17T10:15:00",
            "Number": "0002388 ",
            "Контрагент_Key": SUP_KEY,
            "Склад_Key": WH_KEY,
            "Ответственный_Key": USER_KEY,
            "СуммаВключаетНДС": False,
            "Товары": [
                {
                    "Номенклатура_Key": ITEM_KEY,
                    "Количество": 4.246,
                    "Цена": 73695.0,
                    "Сумма": 312909.0,
                    "СуммаНДС": 37549.08,
                }
            ],
        }
        return httpx.Response(200, json={"value": [doc]})
    names = {
        "Catalog_Контрагенты": {"Ref_Key": SUP_KEY, "Description": "ИП Сыдыков А.Т.", "ИНН": "20909197900124"},
        "Catalog_Номенклатура": {
            "Ref_Key": ITEM_KEY,
            "Description": "Арматура А500С ф14",
            "ЕдиницаИзмерения_Key": UNIT_KEY,
        },
        "Catalog_КлассификаторЕдиницИзмерения": {"Ref_Key": UNIT_KEY, "Description": "т"},
        "Catalog_Склады": {"Ref_Key": WH_KEY, "Description": "ЖК «Ордо»"},
        "Catalog_Пользователи": {"Ref_Key": USER_KEY, "Description": "Асанов Б."},
    }
    for cat, row in names.items():
        if path.endswith(cat):
            return httpx.Response(200, json={"value": [row]})
    return httpx.Response(404)


async def test_odata_maps_documents_to_contract():
    transport = httpx.MockTransport(odata_handler)
    async with OneCOData("https://1c.example.kg/bp/", "audit", "secret", PROFILES["bp30"], transport=transport) as c:
        assert (await c.detect_profile()).key == "bp30"
        lines = await c.purchases(date(2026, 1, 1), date(2026, 1, 31))
    assert c.root == "https://1c.example.kg/bp/odata/standard.odata/"
    assert lines == [
        {
            "date": "2026-01-17",
            "doc": "Поступление товаров и услуг 0002388 от 17.01.2026",
            "supplier": "ИП Сыдыков А.Т.",
            "supplier_inn": "20909197900124",
            "item": "Арматура А500С ф14",
            "unit": "т",
            "qty": "4.246",
            "price": "73695.0",
            "amount": "350458.08",
            "vat": "37549.08",
            "object": "ЖК «Ордо»",
            "responsible": "Асанов Б.",
            "ref": DOC_KEY,
        }
    ]  # в БП «Сумма» без НДС, если цена его не включает: в строку идёт сумма с НДС


async def test_odata_connection_sync_creates_run(org, as_user):
    acc = await as_user("acc@t.kg")
    body = {
        "name": "Бухгалтерия",
        "base_url": "https://1c.example.kg/bp",
        "username": "audit",
        "password": "secret",
        "profile": "bp30",
        "period_days": 90,
    }
    created = (await acc.post("/api/integrations/connections", json=body)).json()
    assert created["has_password"] and "secret" not in json.dumps(created)  # пароль наружу не отдаётся
    run_id = await svc.sync_connection(
        __import__("uuid").UUID(created["id"]),
        date(2026, 1, 1),
        date(2026, 1, 31),
        transport=httpx.MockTransport(odata_handler),
    )
    conn = (await acc.get("/api/integrations/connections")).json()[0]
    assert conn["last_status"] == "ok" and conn["last_run_id"] == str(run_id)
    run = (await acc.get(f"/api/audits/{run_id}")).json()
    assert run["status"] == "done" and run["lines_total"] == 1


async def test_odata_errors_are_user_facing(org, as_user):
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    acc = await as_user("acc@t.kg")
    created = (
        await acc.post(
            "/api/integrations/connections",
            json={"name": "Чужая", "base_url": "https://1c.example.kg/x", "username": "u", "password": "p"},
        )
    ).json()
    run_id = await svc.sync_connection(__import__("uuid").UUID(created["id"]), transport=httpx.MockTransport(deny))
    assert run_id is None
    conn = next(c for c in (await acc.get("/api/integrations/connections")).json() if c["id"] == created["id"])
    assert conn["last_status"] == "error" and "логин или пароль" in conn["last_error"]


# --- MCP ------------------------------------------------------------------------------------------


async def test_mcp_tools_for_agents(app, token, as_user):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        r = await raw.post("/api/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401

    agent_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token['token']}"},
    )
    async with (
        agent_http,
        streamable_http_client("http://test/api/mcp/", http_client=agent_http) as (rd, wr, _),
        ClientSession(rd, wr) as session,
    ):
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {
            "list_audits",
            "audit_summary",
            "list_questions",
            "get_question",
            "price_history",
            "submit_purchases",
            "audit_status",
        } <= tools
        prompts = {p.name for p in (await session.list_prompts()).prompts}
        assert "export_purchases_from_1c" in prompts

        res = await session.call_tool("submit_purchases", {"lines": synthetic_lines()[:300], "source": "mcp"})
        assert not res.isError, res.content
        audit_id = json.loads(res.content[0].text)["audit_id"]
        status = json.loads((await session.call_tool("audit_status", {"audit_id": audit_id})).content[0].text)
        assert status["status"] == "done"
        summary = json.loads((await session.call_tool("audit_summary", {"audit_id": audit_id})).content[0].text)
        assert summary["audit"]["lines"] == 300 and "top_waiting" in summary
        if summary["top_waiting"]:
            q = summary["top_waiting"][0]
            detail = json.loads((await session.call_tool("get_question", {"question_id": q["id"]})).content[0].text)
            assert detail["lines"]


async def test_answer_review_hint_for_owner(token, as_user, app, fake_llm):
    """Снабженец ответил → в фоне модуль answer_review → подсказка видна собственнику, статус не меняется."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw:
        r = await raw.post(
            "/api/integrations/purchases",
            headers={"Authorization": f"Bearer {token['token']}"},
            json={"lines": synthetic_lines()},
        )
        run_id = r.json()["run_id"]
    buyer, owner = await as_user("buyer@t.kg"), await as_user("owner@t.kg")
    mine = (await buyer.get("/api/findings", params={"run_id": run_id, "status": "open"})).json()["items"]
    f = next(x for x in mine if float(x["overpayment"]) > 0)
    fake = fake_llm(
        {
            "answer_review": [
                {
                    "verdict": "partial",
                    "reasons_detected": ["Срочная поставка"],
                    "checkable": ["накладная с датой доставки"],
                    "followup": "Почему не заказали у основного поставщика заранее?",
                    "summary": "Срочность названа, но не подтверждена.",
                }
            ]
        }
    )
    r = await buyer.post(
        f"/api/findings/{f['id']}/answer",
        json={"answer": "Нужно было срочно на объект", "reasons": ["Срочная поставка"]},
    )
    assert r.status_code == 200
    detail = (await owner.get(f"/api/findings/{f['id']}")).json()
    assert detail["status"] == "answered"
    assert detail["answer_review"]["verdict"] == "partial" and detail["answer_review"]["version"].startswith(
        "answer_review/"
    )
    assert "Нужно было срочно на объект" in fake.calls[0][2]  # ответ ушёл в модель внутри блока данных
    digest = (await owner.get(f"/api/audits/{run_id}/digest")).json()
    assert digest["by"] == "template" and "млн сом" in digest["text"]
