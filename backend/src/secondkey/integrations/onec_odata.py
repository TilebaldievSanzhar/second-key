"""1С по стандартному интерфейсу OData: документы поступления → строки закупок (контракт PurchaseIn).

Работает с опубликованной базой (веб-сервер, 1С:Fresh, облако) — нужна публикация OData и пользователь
с правом чтения. Профили покрывают типовые конфигурации; имена полей можно переопределить в настройках
подключения (config["fields"]), сверившись с $metadata конкретной базы.

    БП 3.0 (в т.ч. редакции для КР/КЗ):  Document_ПоступлениеТоваровУслуг, ТЧ «Товары»
    УТ 11 / ERP / КА 2:                  Document_ПриобретениеТоваровУслуг, ТЧ «Товары»
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from secondkey.config import settings
from secondkey.observability import log


@dataclass(frozen=True)
class Profile:
    key: str
    title: str
    document: str
    table: str = "Товары"
    doc_fields: dict[str, str] = field(default_factory=dict)  # наше поле → поле документа
    line_fields: dict[str, str] = field(default_factory=dict)  # наше поле → поле строки ТЧ
    catalogs: dict[str, str] = field(default_factory=dict)  # наше поле → справочник для расшифровки ключа


PROFILES: dict[str, Profile] = {
    "bp30": Profile(
        key="bp30",
        title="1С:Бухгалтерия 3.0",
        document="Document_ПоступлениеТоваровУслуг",
        doc_fields={
            "supplier": "Контрагент_Key",
            "object": "Склад_Key",
            "responsible": "Ответственный_Key",
            "vat_included": "СуммаВключаетНДС",
        },
        line_fields={
            "item": "Номенклатура_Key",
            "qty": "Количество",
            "price": "Цена",
            "amount": "Сумма",
            "vat": "СуммаНДС",
        },
        catalogs={
            "supplier": "Catalog_Контрагенты",
            "object": "Catalog_Склады",
            "responsible": "Catalog_Пользователи",
            "item": "Catalog_Номенклатура",
            "unit": "Catalog_КлассификаторЕдиницИзмерения",
        },
    ),
    "ut11": Profile(
        key="ut11",
        title="1С:Управление торговлей 11, ERP, КА 2",
        document="Document_ПриобретениеТоваровУслуг",
        doc_fields={
            "supplier": "Контрагент_Key",
            "object": "Склад_Key",
            "responsible": "Менеджер_Key",
            "vat_included": "ЦенаВключаетНДС",
        },
        line_fields={
            "item": "Номенклатура_Key",
            "qty": "Количество",
            "price": "Цена",
            "amount": "СуммаСНДС",
            "vat": "СуммаНДС",
        },
        catalogs={
            "supplier": "Catalog_Контрагенты",
            "object": "Catalog_Склады",
            "responsible": "Catalog_Пользователи",
            "item": "Catalog_Номенклатура",
            "unit": "Catalog_УпаковкиЕдиницыИзмерения",
        },
    ),
}


class ODataError(Exception):
    """Ошибка с текстом для пользователя: что не так с подключением."""


def odata_root(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if "/odata/standard.odata" in base:
        return base.split("/odata/standard.odata")[0] + "/odata/standard.odata/"
    return base + "/odata/standard.odata/"


def _dt(d: date, end: bool = False) -> str:
    return f"datetime'{d.isoformat()}T{'23:59:59' if end else '00:00:00'}'"


class OneCOData:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        profile: Profile,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        page: int = 200,
    ):
        self.root = odata_root(base_url)
        self.profile = profile
        self.page = page
        self.http = httpx.AsyncClient(
            auth=httpx.BasicAuth(username, password),
            timeout=60,
            transport=transport,
            headers={"User-Agent": settings.http_user_agent, "Accept": "application/json"},
        )

    async def __aenter__(self) -> OneCOData:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()

    async def _get(self, path: str) -> Any:
        url = self.root + path
        try:
            r = await self.http.get(url)
        except httpx.HTTPError as exc:
            raise ODataError(f"1С не отвечает по адресу {self.root}: {type(exc).__name__}") from exc
        if r.status_code == 401:
            raise ODataError("1С отклонила логин или пароль")
        if r.status_code == 404:
            raise ODataError(f"В публикации OData нет объекта {path.split('?')[0]} — включите его в составе OData")
        if r.status_code >= 400:
            raise ODataError(f"1С ответила {r.status_code}: {r.text[:200]}")
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    async def detect_profile(self) -> Profile | None:
        """По $metadata: какой документ поступления опубликован."""
        meta = await self._get("$metadata")
        text = meta if isinstance(meta, str) else ""
        for p in PROFILES.values():
            if f'Name="{p.document}"' in text:
                return p
        return None

    async def documents(self, start: date, end: date) -> list[dict[str, Any]]:
        p = self.profile
        select = ",".join(["Ref_Key", "Date", "Number", *p.doc_fields.values(), p.table])
        flt = f"Date ge {_dt(start)} and Date le {_dt(end, True)} and Posted eq true and DeletionMark eq false"
        out: list[dict[str, Any]] = []
        skip = 0
        while True:
            q = (
                f"{p.document}?$format=json&$filter={quote(flt)}&$select={quote(select)}"
                f"&$orderby=Date&$top={self.page}&$skip={skip}"
            )
            page = (await self._get(q)).get("value", [])
            out.extend(page)
            if len(page) < self.page:
                return out
            skip += self.page

    async def _names(self, catalog: str, keys: set[str], extra: str = "") -> dict[str, dict[str, Any]]:
        names: dict[str, dict[str, Any]] = {}
        keys = {k for k in keys if k and k != "00000000-0000-0000-0000-000000000000"}
        ks = sorted(keys)
        for i in range(0, len(ks), 40):
            flt = " or ".join(f"Ref_Key eq guid'{k}'" for k in ks[i : i + 40])
            sel = "Ref_Key,Description" + (f",{extra}" if extra else "")
            data = await self._get(f"{catalog}?$format=json&$filter={quote(flt)}&$select={quote(sel)}")
            for row in data.get("value", []):
                names[row["Ref_Key"]] = row
        return names

    async def purchases(self, start: date, end: date) -> list[dict[str, Any]]:
        """Строки закупок в формате контракта PurchaseIn."""
        p = self.profile
        docs = await self.documents(start, end)
        df, lf, cat = p.doc_fields, p.line_fields, p.catalogs
        keys: dict[str, set[str]] = {k: set() for k in ("supplier", "object", "responsible", "item")}
        for d in docs:
            for k in ("supplier", "object", "responsible"):
                if df.get(k):
                    keys[k].add(d.get(df[k]) or "")
            for ln in d.get(p.table) or []:
                keys["item"].add(ln.get(lf["item"]) or "")
        suppliers = await self._names(cat["supplier"], keys["supplier"], "ИНН")
        objects = await self._names(cat["object"], keys["object"]) if df.get("object") else {}
        people = await self._names(cat["responsible"], keys["responsible"]) if df.get("responsible") else {}
        items = await self._names(cat["item"], keys["item"], "ЕдиницаИзмерения_Key")
        units = await self._names(cat["unit"], {i.get("ЕдиницаИзмерения_Key") or "" for i in items.values()})

        lines: list[dict[str, Any]] = []
        for d in docs:
            sup = suppliers.get(d.get(df.get("supplier", "")) or "", {})
            when = datetime.fromisoformat(d["Date"]).date()
            vat_in = bool(d.get(df.get("vat_included", ""), True))
            for ln in d.get(p.table) or []:
                item = items.get(ln.get(lf["item"]) or "", {})
                amount = Decimal(str(ln.get(lf["amount"]) or 0))
                vat = Decimal(str(ln.get(lf.get("vat", "")) or 0))
                if not vat_in and lf["amount"] == "Сумма":
                    amount += vat  # в БП «Сумма» без НДС, если цена не включает НДС
                lines.append(
                    {
                        "date": when.isoformat(),
                        "doc": f"{_doc_title(p)} {d.get('Number', '').strip()} от {when:%d.%m.%Y}",
                        "supplier": sup.get("Description") or "Поставщик не указан",
                        "supplier_inn": sup.get("ИНН") or None,
                        "item": item.get("Description") or "Номенклатура не указана",
                        "unit": units.get(item.get("ЕдиницаИзмерения_Key") or "", {}).get("Description"),
                        "qty": str(ln.get(lf["qty"]) or 0),
                        "price": str(ln.get(lf["price"])) if ln.get(lf["price"]) is not None else None,
                        "amount": str(amount),
                        "vat": str(vat) if vat else None,
                        "object": objects.get(d.get(df.get("object", "")) or "", {}).get("Description"),
                        "responsible": people.get(d.get(df.get("responsible", "")) or "", {}).get("Description"),
                        "ref": d.get("Ref_Key"),
                    }
                )
        log.info("onec.odata_pulled", profile=p.key, documents=len(docs), lines=len(lines))
        return lines


def _doc_title(p: Profile) -> str:
    return "Поступление товаров и услуг" if p.key == "bp30" else "Приобретение товаров и услуг"


def profile_from_config(config: dict[str, Any]) -> Profile:
    base = PROFILES.get(config.get("profile") or "bp30", PROFILES["bp30"])
    over = config.get("fields") or {}
    if not over:
        return base
    return Profile(
        key=base.key,
        title=base.title,
        document=over.get("document", base.document),
        table=over.get("table", base.table),
        doc_fields={**base.doc_fields, **over.get("doc_fields", {})},
        line_fields={**base.line_fields, **over.get("line_fields", {})},
        catalogs={**base.catalogs, **over.get("catalogs", {})},
    )
