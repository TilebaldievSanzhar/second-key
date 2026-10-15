"""Нормализация номенклатуры закупок: строка учёта → позиция + цена за базовую единицу.

Детерминированный первый проход (без LLM):
1. материал («голова») по словарю: «Арм. ф12 А500С» → арматура;
2. различающие атрибуты: диаметр, марка/класс, габариты, фракция, толщина;
3. подпись позиции = материал + атрибуты; одинаковая подпись = одна позиция;
4. единица → базовая единица материала (кг → т, мешок 50 кг → т, м → т для арматуры).

Правило безопасности то же, что в матчере procdesk: лучше разделить одну позицию
на две группы (недосчитаем находки), чем склеить разные товары (ложная переплата).
Нет материала или обязательного атрибута → needs_input: строку разбирает человек
или LLM-каскад по каталогу (шаг 1.3b), система не угадывает.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from secondkey.engine.models import NormLine, PurchaseLine
from secondkey.engine.units import REBAR_KG_PER_M

# (материал, регэксп, обязательные атрибуты, базовая единица)
# Порядок важен: специфичное раньше общего («пескобетон» — смесь, не бетон и не песок).
MATERIALS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("грунтовка", r"грунтовк|бетоноконтакт|праймер", (), "l"),
    ("газоблок", r"газоблок|газобетон|пеноблок|газосиликат", ("dims",), "pcs"),
    ("сухая смесь", r"пескобетон|сухая смесь|\bцпс\b", (), "kg"),
    ("клей плиточный", r"клей\s+плит|плиточн\w*\s+клей|клей для плит", (), "kg"),
    ("шпаклевка", r"шпа[кт]л[её]в|шпакл", (), "kg"),
    ("штукатурка", r"штукатурк", (), "kg"),
    ("цемент", r"цемент|\bпц\s?-?\s?\d|\bпц\b|\bцем\b|цем\.", ("mark",), "t"),
    ("проволока", r"проволок", (), "kg"),
    ("арматура", r"арматур|\bарм\b|арм\.|ар-ра|сталь арматурн|прокат арматурн", ("diameter",), "t"),
    ("сетка", r"сетк", ("dims",), "m2"),
    ("пгс", r"\bпгс\b|песчано-?гравий|гравийно-?песчан", (), "m3"),
    ("песок", r"песок|песк[аиу]\b|песч", (), "m3"),
    ("щебень", r"щеб[её]н|щебн", (), "m3"),
    ("раствор", r"раствор", ("mark",), "m3"),
    ("бетон", r"бетон|\bбсг\b", ("mark",), "m3"),
    ("кирпич", r"кирпич", (), "pcs"),
    ("кабель", r"кабел|\bа?ввг", ("dims",), "m"),
    ("провод", r"провод|\bпвс\b|\bпугв", ("dims",), "m"),
    ("профнастил", r"профнастил|профлист|профилированн\w*\s+лист", (), "m2"),
    ("труба", r"труб", (), "m"),
    ("гипсокартон", r"гипсокартон|\bгкл\b|гипсокарт|гипсо-картон", (), "sheet"),
    ("профиль", r"профил[ья]", ("dims",), "pcs"),
    ("краска", r"краск|эмаль|водоэмульс", (), "kg"),
    ("доска", r"доск|пиломатериал", ("dims",), "m3"),
    ("брус", r"брус", ("dims",), "m3"),
    ("фанера", r"фанер", (), "sheet"),
    ("утеплитель", r"утеплит|минват|минплит|минераловат|пенопласт|пеноплекс|экструд", (), "m2"),
    ("уголок", r"уголок|уголк", ("dims",), "m"),
    ("электроды", r"электрод", (), "kg"),
    ("саморезы", r"саморез", ("dims",), "kg"),
    ("дюбель", r"дюбел", ("dims",), "pcs"),
]
_MATERIAL_RES = [(h, re.compile(p, re.IGNORECASE), req, base) for h, p, req, base in MATERIALS]
BASE_UNIT = {h: base for h, _, _, base in MATERIALS}

# латиница-омоглифы → кириллица (копипаста из Excel: «A500C» латиницей)
_LAT2CYR = str.maketrans("AaBCcEeHKMOoPpTXxy", "АаВСсЕеНКМОоРрТХху")

_RE_DIAMETER = re.compile(
    r"(?:[⌀øØ]|(?<![а-яa-z])[фdд](?![а-яa-z]))\s*[-–.]?\s*(\d{1,3}(?:[.,]\d)?)|диаметр\w*\s*[-–]?\s*(\d{1,3})",
    re.IGNORECASE,
)
_RE_DIMS = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[хx*×]\s*(\d+(?:[.,]\d+)?)(?:\s*[хx*×]\s*(\d+(?:[.,]\d+)?))?", re.IGNORECASE
)
_RE_FRACTION = re.compile(r"(?<!\d)(?<!\d[.,])(\d{1,2})\s*[-–/]\s*(\d{1,3})(?![\d.,])")
FRACTION_ENDS = {5, 10, 15, 20, 40, 70, 80, 120}
_RE_DU = re.compile(r"\bду\s?-?\s?(\d{1,3})\b", re.IGNORECASE)
_RE_CABLE_TYPE = re.compile(r"\b(а?ввг[\wА-Яа-я()\-]*|nym|пвс|пугв|кввг|сип[\w\-]*)", re.IGNORECASE)
_RE_THICKNESS = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,2})?)\s*мм", re.IGNORECASE)
_RE_PROFNASTIL = re.compile(r"(?<![а-яa-z])[СC]\s?-?\s?(\d{1,2})(?!\d)", re.IGNORECASE)
_RE_PACK_KG = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*кг", re.IGNORECASE)
_RE_PACK_L = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*л\b", re.IGNORECASE)
_RE_PN = re.compile(r"\bPN\s?-?(\d{2})\b", re.IGNORECASE)

# марки: цемент М400/ПЦ400, бетон М300/В22,5, арматура А500С/А400/А-III, газоблок D500, электроды МР-3
_RE_REBAR_CLASS = re.compile(r"\b(А|A)\s?-?\s?(500\s?[СC]?|400|240|III|II|I|3|1)(?![\d])", re.IGNORECASE)
_RE_M_GRADE = re.compile(r"(?<![а-яa-z])(?:М|M|ПЦ|PC)\s?-?\s?(\d{2,3})(?!\d)", re.IGNORECASE)
_RE_B_CLASS = re.compile(r"(?<![а-яa-z])(?:В|B)\s?-?\s?(\d{1,2}(?:[.,]5)?)(?![\d])", re.IGNORECASE)
_RE_DENSITY = re.compile(r"(?<![а-яa-z])(?:D|Д)\s?-?\s?(\d{3})(?!\d)", re.IGNORECASE)
_RE_ELECTRODE = re.compile(r"\b(МР|MP|АНО|УОНИ)\s?-?\s?(\d{1,2})\b", re.IGNORECASE)

# класс бетона → марка (соответствие по ГОСТ 26633, типовая таблица)
B_TO_M = {"15": "200", "20": "250", "22.5": "300", "25": "350", "30": "400", "35": "450"}
REBAR_SIZES = set(REBAR_KG_PER_M)
PPR_SIZES = {16, 20, 25, 32, 40, 50, 63, 75, 90, 110}

UNIT_SYNONYMS = {
    "т": "t",
    "тн": "t",
    "тонна": "t",
    "тонн": "t",
    "tn": "t",
    "кг": "kg",
    "килограмм": "kg",
    "м3": "m3",
    "м³": "m3",
    "куб": "m3",
    "куб.м": "m3",
    "м.куб": "m3",
    "кубометр": "m3",
    "м2": "m2",
    "м²": "m2",
    "кв.м": "m2",
    "м": "m",
    "пог.м": "m",
    "п.м": "m",
    "пм": "m",
    "метр": "m",
    "шт": "pcs",
    "штук": "pcs",
    "штука": "pcs",
    "дана": "pcs",
    "меш": "bag",
    "мешок": "bag",
    "мешков": "bag",
    "мкр": "bag",
    "л": "l",
    "литр": "l",
    "лист": "sheet",
    "листов": "sheet",
    "упак": "pack",
    "уп": "pack",
    "пчк": "pack",
    "пачка": "pack",
    "флак": "pack",
    "кан": "pack",
    "канистра": "pack",
    "ведро": "pack",
    "банка": "pack",
}


def canon_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    u = raw.strip().lower().rstrip(".")
    return UNIT_SYNONYMS.get(u, u)


def detect_material(text: str) -> str | None:
    for head, rx, _, _ in _MATERIAL_RES:
        if rx.search(text):
            return head
    return None


def _num(s: str) -> str:
    v = Decimal(s.replace(",", "."))
    return format(v.normalize(), "f")


def extract_attrs(head: str, text: str) -> dict[str, str]:
    """Различающие атрибуты позиции. Значения нормализованы; упаковка сюда не входит."""
    t = text.translate(_LAT2CYR) if head in ("арматура", "цемент", "бетон", "раствор") else text
    attrs: dict[str, str] = {}

    if head == "арматура":
        m = _RE_DIAMETER.search(t)
        d = (m.group(1) or m.group(2)) if m else None
        if d is None:  # «Арматура 12 мм А500С», «Арм. А500С 12»: первое число из сортамента
            for n in re.findall(r"(?<![\d.,А-Яа-яA-Za-z])(\d{1,2})(?![\d.,])", re.sub(_RE_REBAR_CLASS, " ", t)):
                if int(n) in REBAR_SIZES:
                    d = n
                    break
        if d and int(float(d.replace(",", "."))) in REBAR_SIZES:
            attrs["d"] = str(int(float(d.replace(",", "."))))
        c = _RE_REBAR_CLASS.search(t)
        if c:
            raw = c.group(2).upper().replace(" ", "").replace("C", "С")
            attrs["cls"] = {"III": "А400", "3": "А400", "II": "А300", "I": "А240", "1": "А240"}.get(raw, f"А{raw}")
        return attrs

    if head in ("цемент", "бетон", "раствор"):
        m = _RE_M_GRADE.search(t)
        if m:
            attrs["m"] = f"М{m.group(1)}"
        elif head == "бетон":
            b = _RE_B_CLASS.search(t)
            if b and _num(b.group(1)) in B_TO_M:
                attrs["m"] = f"М{B_TO_M[_num(b.group(1))]}"
        return attrs

    if head in ("щебень", "пгс"):
        for m in _RE_FRACTION.finditer(re.sub(r"гост\s*\S+", " ", text, flags=re.IGNORECASE)):
            a, b = int(m.group(1)), int(m.group(2))
            if a < b and b in FRACTION_ENDS:
                attrs["fr"] = f"{a}-{b}"
                break
        return attrs

    if head in ("кабель", "провод"):
        ct = _RE_CABLE_TYPE.search(text)
        if ct:
            attrs["type"] = re.sub(
                r"[^а-яa-z]", "", ct.group(1).lower().translate(str.maketrans("abcehkmoptxy", "авсенкмортху"))
            )

    if head == "газоблок":
        dm = _RE_DENSITY.search(text)
        if dm:
            attrs["D"] = dm.group(1)

    if head == "электроды":
        e = _RE_ELECTRODE.search(text.translate(_LAT2CYR))
        if e:
            attrs["m"] = f"{e.group(1).upper()}-{e.group(2)}"
        dm = _RE_DIAMETER.search(text)
        if dm:
            attrs["d"] = _num(dm.group(1) or dm.group(2))
        return attrs

    if head in ("труба",):
        du = _RE_DU.search(text)
        if du:
            attrs["du"] = du.group(1)  # условный проход ≠ наружный диаметр: свой ключ
        else:
            dm = _RE_DIAMETER.search(text)
            if dm:
                attrs["d"] = _num(dm.group(1) or dm.group(2))
            elif re.search(r"ппр|ppr|полипроп", text, re.IGNORECASE):
                # «Труба полипропиленовая 20 PN20»: голое число из сортамента ППР, не номер PN
                for n in re.findall(r"(?<![\d.,A-Za-zА-Яа-я])(\d{2,3})(?![\d.,])", _RE_PN.sub(" ", text)):
                    if int(n) in PPR_SIZES:
                        attrs["d"] = n
                        break
        pn = _RE_PN.search(text)
        if pn:
            attrs["pn"] = pn.group(1)

    dims = _RE_DIMS.search(text)
    if dims:
        attrs["s"] = "x".join(_num(g) for g in dims.groups() if g)
    if head == "профнастил":
        pm = _RE_PROFNASTIL.search(text)
        if pm:
            attrs["m"] = f"С{pm.group(1)}"
    if head == "песок":
        low = text.lower()
        kind = "мытый" if "мыт" in low else "карьерный" if "карьер" in low else "речной" if "речн" in low else None
        if kind:
            attrs["kind"] = kind
    if head in ("гипсокартон", "фанера", "профнастил", "утеплитель"):
        th = _RE_THICKNESS.search(_RE_DIMS.sub(" ", text))
        if th:
            attrs["t"] = _num(th.group(1))
    return attrs


def _label(head: str, attrs: dict[str, str]) -> str:
    parts = [head.capitalize()]
    if "d" in attrs:
        parts.append(f"⌀{attrs['d']}")
    if "du" in attrs:
        parts.append(f"Ду{attrs['du']}")
    if "type" in attrs:
        parts.append(attrs["type"].upper())
    for k in ("cls", "m", "D", "pn"):
        if k in attrs:
            parts.append(("PN" if k == "pn" else "D" if k == "D" else "") + attrs[k])
    if "fr" in attrs:
        parts.append(f"фр. {attrs['fr']}")
    if "kind" in attrs:
        parts.append(attrs["kind"])
    if "s" in attrs:
        parts.append(attrs["s"].replace("x", "×"))
    if "t" in attrs:
        parts.append(f"{attrs['t']} мм")
    return " ".join(parts)


def _required_ok(head: str, attrs: dict[str, str]) -> bool:
    _, _, req, _ = next(m for m in MATERIALS if m[0] == head)
    if head == "труба":
        return "d" in attrs or "s" in attrs or "du" in attrs
    if head == "кабель" or head == "провод":
        return "s" in attrs
    return all(("d" if r == "diameter" else "m" if r == "mark" else "s") in attrs for r in req)


def _pack_size(text: str) -> tuple[Decimal, str] | None:
    """Вес/объём упаковки из наименования: «мешок 50 кг» → (50, kg)."""
    m = _RE_PACK_KG.search(text)
    if m:
        return Decimal(m.group(1).replace(",", ".")), "kg"
    m = _RE_PACK_L.search(text)
    if m:
        return Decimal(m.group(1).replace(",", ".")), "l"
    return None


def to_base(qty: Decimal, unit: str | None, head: str, attrs: dict[str, str], text: str) -> tuple[Decimal, str] | None:
    """Количество в базовой единице материала; None — перевод невозможен без догадки."""
    base = BASE_UNIT[head]
    u = canon_unit(unit)
    if u == base:
        return qty, base
    if {u, base} == {"t", "kg"}:
        return (qty / 1000, base) if base == "t" else (qty * 1000, base)
    if u in ("bag", "pcs", "pack") and base in ("t", "kg", "l"):
        pack = _pack_size(text)
        if pack is None:
            return None
        size, size_unit = pack
        if size_unit == "kg" and base == "t":
            return qty * size / 1000, base
        if size_unit == base:
            return qty * size, base
        return None
    if base == "sheet" and u == "pcs":
        return qty, base  # листовые: «шт» = лист
    if head == "арматура" and u == "m" and base == "t" and "d" in attrs:
        kg_per_m = REBAR_KG_PER_M[int(attrs["d"])]
        return qty * kg_per_m / 1000, base
    return None


def normalize_line(line: PurchaseLine) -> NormLine:
    text = re.sub(r"\s+", " ", line.item_raw).strip()
    # «Aрматура», «Tруба»: латиница внутри русских слов после копипасты из Excel
    head = detect_material(text.translate(_LAT2CYR))
    if head is None:
        return NormLine(line, None, None, None, None, None, None, "needs_input", "материал не распознан")
    attrs = extract_attrs(head, text)
    if not _required_ok(head, attrs):
        return NormLine(
            line,
            None,
            None,
            head,
            None,
            None,
            None,
            "needs_input",
            "нет различающего атрибута (диаметр, марка или размер) — позиция неоднозначна",
        )
    sku = head + "|" + "|".join(f"{k}={v}" for k, v in sorted(attrs.items()))
    label = _label(head, attrs)
    conv = to_base(line.qty, line.unit_raw, head, attrs, text)
    if conv is None:
        # единица не переводится без догадки: отдельная группа «позиция в своей единице»
        u = canon_unit(line.unit_raw) or "?"
        qty_b, unit_b = line.qty, u
        sku = f"{sku}|u={u}"
        label = f"{label} ({line.unit_raw or 'ед.?'})"
    else:
        qty_b, unit_b = conv
    if qty_b == 0:
        return NormLine(line, sku, label, head, unit_b, None, None, "needs_input", "нулевое количество")
    price_b = (line.amount / qty_b).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return NormLine(line, sku, label, head, unit_b, qty_b, price_b, "ok")


def normalize(lines: list[PurchaseLine]) -> list[NormLine]:
    return [normalize_line(l) for l in lines]


def coverage(norm: list[NormLine]) -> dict:
    st = Counter(n.status for n in norm)
    return {"lines": len(norm), **st, "share_ok": round(st.get("ok", 0) / len(norm), 4) if norm else 0.0}
