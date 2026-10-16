"""ИНН Кыргызстана: что можно узнать о поставщике из самого номера, без реестров.

14 цифр. Первая — тип: 0 — юрлицо (и филиал); 1/2 — физлицо или ИП, гражданин КР (ж/м);
3/4 — физлицо-иностранец (ж/м, порядок присвоения ПИН с 2025 года). Цифры 2–9 — дата ДДММГГГГ:
у физлица — дата рождения (структура ПИН), у юрлица — дата регистрации. Для юрлиц официального
описания нет — установлено по выборке 262 поставщиков госзакупок (docs/product/SUPPLIER_LINKS.md § 4),
поэтому в текстах — «по номеру ИНН»: в базе ПИН бывают ошибки.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

_KIND = {"0": ("org", None), "1": ("person", "f"), "2": ("person", "m"), "3": ("person", "f"), "4": ("person", "m")}


@dataclass(frozen=True)
class InnInfo:
    inn: str
    kind: str  # org | person
    gender: str | None  # f | m — у физлица
    date: date | None  # регистрация юрлица / рождение физлица; None — дата в номере не читается

    def years_at(self, on: date) -> float | None:
        return (on - self.date).days / 365.25 if self.date else None


def clean_inn(raw: object) -> str | None:
    """Только цифры; Excel теряет ведущий ноль у юрлиц («1204201810015» → «01204201810015»)."""
    if raw is None or isinstance(raw, bool):
        return None
    digits = str(int(raw)) if isinstance(raw, (int, float)) else re.sub(r"\D", "", str(raw))
    if len(digits) == 13:
        digits = "0" + digits
    return digits if len(digits) == 14 else None


def parse_inn(raw: object) -> InnInfo | None:
    inn = clean_inn(raw)
    if inn is None or inn[0] not in _KIND:
        return None
    kind, gender = _KIND[inn[0]]
    try:
        d = date(int(inn[5:9]), int(inn[3:5]), int(inn[1:3]))
    except ValueError:
        d = None
    if d is not None and not (1900 <= d.year <= date.today().year + 1):
        d = None
    return InnInfo(inn=inn, kind=kind, gender=gender, date=d)


def inn_hash(salt: str, inn: str) -> str:
    """Хэш ИНН сотрудника: в 1С считается так же (SHA-256 от «соль:ИНН», hex), сами ИНН к нам не уходят.

    Это псевдонимизация, а не анонимизация: соль — у организации и у сервиса.
    """
    return hashlib.sha256(f"{salt}:{inn}".encode()).hexdigest()
