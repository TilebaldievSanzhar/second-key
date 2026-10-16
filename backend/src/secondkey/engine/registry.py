"""Сведения реестра о поставщике — как их видит движок, независимо от источника.

Источники: ручная проверка по выписке Минюста (record.minjust.gov.kg) в карточке поставщика и агрегаторы
по договору (Statsnet, ОсОО.KG) — адаптеры в secondkey/registry. Движок только читает эти сведения.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

INACTIVE = re.compile(r"ликвид|банкрот|прекращ|недейств|исключ|liquidat|bankrupt|inactive|dissolv", re.I)


@dataclass
class RegistryRecord:
    inn: str
    source: str  # manual | statsnet | …
    name: str | None = None
    status: str | None = None  # как в источнике: «Действующее», «Ликвидировано», «Active»…
    active: bool | None = None
    registered: date | None = None  # дата регистрации по реестру
    director: str | None = None
    founders: list[str] = field(default_factory=list)
    address: str | None = None
    unreliable: bool | None = None  # в реестре недобросовестных поставщиков госзакупок
    risks: list[str] = field(default_factory=list)  # риски, которые отмечает агрегатор
    checked_at: date | None = None
    url: str | None = None

    @property
    def inactive(self) -> bool:
        return self.active is False or bool(self.status and INACTIVE.search(self.status))

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("registered", "checked_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RegistryRecord:
        def day(v: object) -> date | None:
            if isinstance(v, date):
                return v
            try:
                return date.fromisoformat(str(v)[:10]) if v else None
            except ValueError:
                return None

        return cls(
            inn=str(d.get("inn") or ""),
            source=str(d.get("source") or "manual"),
            name=d.get("name") or None,
            status=d.get("status") or None,
            active=d.get("active"),
            registered=day(d.get("registered")),
            director=d.get("director") or None,
            founders=[str(x) for x in d.get("founders") or [] if str(x).strip()],
            address=d.get("address") or None,
            unreliable=d.get("unreliable"),
            risks=[str(x) for x in d.get("risks") or []],
            checked_at=day(d.get("checked_at")),
            url=d.get("url") or None,
        )


def person_key(name: str | None) -> str | None:
    """«Сыдыков Азамат Талантбекович», «СЫДЫКОВ А.Т.» → «сыдыков а т»: фамилия и инициалы.

    Только с инициалами: одна фамилия совпадает слишком часто.
    """
    if not name:
        return None
    tokens = [t for t in re.split(r"[\s.,]+", name.lower().replace("ё", "е")) if t]
    if len(tokens) < 2:
        return None
    return " ".join([tokens[0], *[t[0] for t in tokens[1:3]]])
