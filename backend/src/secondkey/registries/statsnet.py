"""Statsnet (statsnet.co): компании Казахстана, Кыргызстана и Узбекистана по REST API, ключ — по подписке.

По документации (statsnet.co/en/docs): поиск — POST /companies/search {"query", "filters": {"jurisdiction": ["kg"]}};
карточка — GET /companies/{id}: company.title/name, identifiers[{type, value}], incorporation_date, status,
inactive, officers[], addresses[]; risks{company, shareholders, officers, all}; связи (учредители) —
GET /companies/{id}/relations, платно. Ошибки — {"status", "message"}.

Ответы читаются терпимо: поле, которого нет, просто не попадает в запись. Сверить на пробном ключе
(docs/product/SUPPLIER_LINKS.md § 9): какие поля приходят по КР и как называются должности.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx

from secondkey.config import settings
from secondkey.engine.registry import RegistryRecord
from secondkey.observability import log
from secondkey.registries import RegistryError

HEAD = re.compile(r"руковод|директор|председат|director|head|chairman|ceo|manager", re.I)
UNRELIABLE = re.compile(r"недобросовест|unreliable|unscrupulous|blacklist", re.I)


def _items(body: Any) -> list[dict]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in ("companies", "items", "results", "data", "hits"):
            if isinstance(body.get(k), list):
                return [x for x in body[k] if isinstance(x, dict)]
    return []


def _ids(company: dict) -> set[str]:
    out = {str(company.get(k) or "") for k in ("identifier", "inn", "bin", "iin")}
    for x in company.get("identifiers") or []:
        if isinstance(x, dict) and x.get("value"):
            out.add(str(x["value"]))
    return {re.sub(r"\D", "", x) for x in out if x}


def _name(x: Any) -> str | None:
    if isinstance(x, str):
        return x.strip() or None
    if isinstance(x, dict):
        for k in ("name", "full_name", "title", "fio", "value"):
            if isinstance(x.get(k), str) and x[k].strip():
                return x[k].strip()
    return None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def record_from(inn: str, body: dict, relations: Any = None) -> RegistryRecord:
    """Ответ GET /companies/{id} (и связи) → RegistryRecord."""
    c = body.get("company") if isinstance(body.get("company"), dict) else body
    officers = [o for o in c.get("officers") or [] if isinstance(o, (dict, str))]
    head = next(
        (
            _name(o)
            for o in officers
            if isinstance(o, dict) and HEAD.search(str(o.get("position") or o.get("role") or ""))
        ),
        None,
    ) or (_name(officers[0]) if officers else None)
    founders: list[str] = []
    for rel in _items(relations) if relations is not None else []:
        kind = str(rel.get("connection") or rel.get("type") or rel.get("relation") or "")
        if re.search(r"shareholder|founder|учредит", kind, re.I):
            for x in rel.get("items") or rel.get("entities") or [rel]:
                n = _name(x)
                if n and n not in founders:
                    founders.append(n)
    risks_raw = body.get("risks") if isinstance(body.get("risks"), dict) else {}
    risks = [r for r in (_name(x) for x in risks_raw.get("company") or []) if r]
    addresses = c.get("addresses") or []
    address = None
    if addresses:
        a = addresses[0]
        address = (
            a
            if isinstance(a, str)
            else next(
                (a.get(k) for k in ("address", "full", "value", "text") if isinstance(a, dict) and a.get(k)), None
            )
        )
    inactive = c.get("inactive")
    return RegistryRecord(
        inn=inn,
        source="statsnet",
        name=c.get("title") or c.get("name"),
        status=c.get("status"),
        active=(not inactive) if isinstance(inactive, bool) else None,
        registered=_date(c.get("incorporation_date")),
        director=head,
        founders=founders,
        address=address,
        unreliable=True if any(UNRELIABLE.search(r) for r in risks) else None,
        risks=[r for r in risks if not UNRELIABLE.search(r)][:10],
        checked_at=date.today(),
        url=f"https://statsnet.co/companies/kg/{inn}",
    )


class StatsnetProvider:
    name = "statsnet"

    def __init__(
        self, api_key: str, base_url: str = "https://statsnet.co/api/v1", *, transport=None, timeout: float = 30
    ):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": settings.http_user_agent},
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise RegistryError(f"Statsnet не отвечает: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise RegistryError("Statsnet отклонил ключ или в тарифе нет этих данных")
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise RegistryError("Statsnet: превышен лимит запросов, попробуйте позже")
        if r.status_code >= 400:
            raise RegistryError(f"Statsnet ответил {r.status_code}: {r.text[:200]}")
        return r.json()

    async def lookup(self, inn: str) -> RegistryRecord | None:
        found = await self._call("POST", "/companies/search", json={"query": inn, "filters": {"jurisdiction": ["kg"]}})
        company = next((c for c in _items(found) if inn in _ids(c)), None)
        if company is None or company.get("id") is None:
            return None
        body = await self._call("GET", f"/companies/{company['id']}")
        if not isinstance(body, dict):
            return None
        try:
            relations = await self._call("GET", f"/companies/{company['id']}/relations")
        except RegistryError as exc:  # связи — платно; без них запись без учредителей
            log.info("registry.statsnet_relations_skipped", error=str(exc)[:200])
            relations = None
        return record_from(inn, body, relations)
