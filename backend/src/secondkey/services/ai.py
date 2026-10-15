"""ИИ-сценарии поверх модулей промптов. Модель только предлагает — код проверяет и решает.

У каждого сценария есть защита от выдумки и путь без модели:
- колонки: индексы в пределах строки, поле — одной колонке, обязательные поля на месте; иначе подсказки нет;
- нормализация: в каноническом названии нет чисел, которых не было в исходном, и разборщик движка его принимает;
- проверка ответа: только подсказка собственнику, статус вопроса не меняет;
- дайджест: каждое число из текста модели есть в фактах; иначе — шаблон fallback.j2.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from secondkey import llm
from secondkey.db.models import Finding, PurchaseLine
from secondkey.db.session import session_scope
from secondkey.engine.importer import missing_fields
from secondkey.engine.models import NormLine
from secondkey.engine.normalize import MATERIALS, normalize_line
from secondkey.observability import log
from secondkey.prompts import answer_review, column_mapping, normalize_item, owner_digest, render

# --- колонки ------------------------------------------------------------------------------------

FIELDS = [
    ("date", "Дата", True, "дата документа поступления"),
    ("doc_no", "Документ", False, "номер и дата документа, «Поступление товаров и услуг 0001021 от 04.10.2025»"),
    ("supplier", "Поставщик", True, "контрагент, продавец"),
    ("supplier_inn", "ИНН поставщика", False, "12–14 цифр"),
    ("item", "Номенклатура", True, "наименование материала"),
    ("unit", "Единица", False, "т, кг, м3, шт, мешок"),
    ("qty", "Количество", True, "число"),
    ("price", "Цена", False, "цена за единицу; нужна цена или сумма"),
    ("amount", "Сумма", False, "сумма строки с НДС; нужна цена или сумма"),
    ("vat", "НДС", False, "сумма НДС строки"),
    ("object_name", "Объект", False, "объект строительства, склад, подразделение"),
    ("responsible", "Ответственный", False, "снабженец или автор документа"),
]
FIELD_KEYS = {f[0] for f in FIELDS}


async def suggest_mapping(preview: dict) -> dict | None:
    """Подсказка сопоставления колонок для выгрузки, где автоматический поиск шапки не справился."""
    header = preview.get("header_row") or 0
    rows: list[list[str]] = ([preview["columns"]] if header else []) + list(preview.get("sample") or [])
    rows = [r[:40] for r in rows[:8]]
    if not rows:
        return None
    inputs = column_mapping.Inputs(
        fields=[column_mapping.FieldSpec(key=k, label=lbl, required=req, hint=h) for k, lbl, req, h in FIELDS],
        rows=rows,
    )
    try:
        out: column_mapping.Output = await llm.run("column_mapping", inputs)
    except llm.LLMError as exc:
        log.info("ai.mapping_skipped", reason=str(exc)[:200])
        return None
    width = max(len(r) for r in rows)
    mapping = {k: v for k, v in out.mapping.items() if k in FIELD_KEYS and 0 <= v < width}
    if len(set(mapping.values())) != len(mapping) or missing_fields(mapping):
        log.info("ai.mapping_rejected", mapping=out.mapping)
        return None
    if not 0 <= out.header_row <= len(rows):
        return None
    # номер строки шапки в файле: при найденной шапке rows[0] — она сама, иначе sample — первые строки файла
    file_row = header if header and out.header_row <= 1 else (header + out.header_row - 1 if header else out.header_row)
    return {"header_row": file_row, "mapping": mapping, "by": "ai", "notes": out.notes}


# --- нормализация -------------------------------------------------------------------------------

# канонические примеры на материал: тест проверяет, что движок разбирает каждый в тот же материал
EXAMPLES: dict[str, list[str]] = {
    "арматура": ["Арматура А500С ф12", "Арматура А240 ф8"],
    "цемент": ["Цемент М400 мешок 50 кг", "Цемент М500"],
    "бетон": ["Бетон М300", "Бетон В22,5"],
    "раствор": ["Раствор М150"],
    "газоблок": ["Газоблок 600x300x200 D500"],
    "сетка": ["Сетка 100x100x4"],
    "кабель": ["Кабель ВВГнг 3x2,5"],
    "провод": ["Провод ПВС 3x1,5"],
    "профиль": ["Профиль 60x27"],
    "доска": ["Доска 50x150x6000"],
    "брус": ["Брус 100x100x6000"],
    "уголок": ["Уголок 50x50x5"],
    "саморезы": ["Саморезы 3,5x35"],
    "дюбель": ["Дюбель 6x40"],
    "щебень": ["Щебень фр. 5-20"],
    "песок": ["Песок мытый"],
    "гипсокартон": ["Гипсокартон 12,5 мм"],
    "профнастил": ["Профнастил С21 0,5 мм"],
    "шпаклевка": ["Шпаклевка финишная мешок 25 кг"],
    "краска": ["Краска водоэмульсионная 15 кг"],
    "электроды": ["Электроды МР-3 ф3"],
}

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".").lstrip("0") or "0" for n in _NUM.findall(text)}


def _materials() -> list[normalize_item.Material]:
    return [
        normalize_item.Material(head=h, required=list(req), base_unit=base, examples=EXAMPLES.get(h, [h.capitalize()]))
        for h, _, req, base in MATERIALS
    ]


async def normalize_fallback(norm: list[NormLine], limit: int) -> tuple[list[NormLine], int]:
    """Строки needs_input → каноническое название от модели → разбор движком. Возвращает строки и число распознанных."""
    todo: dict[tuple[str, str | None], list[int]] = {}
    for i, nl in enumerate(norm):
        if nl.status == "needs_input":
            todo.setdefault((nl.line.item_raw, nl.line.unit_raw), []).append(i)
    keys = list(todo)[:limit]
    if not keys:
        return norm, 0
    out = list(norm)
    fixed = 0
    materials = _materials()
    for start in range(0, len(keys), 40):
        batch = keys[start : start + 40]
        inputs = normalize_item.Inputs(
            materials=materials,
            lines=[normalize_item.Line(id=str(start + j), item=item, unit=u) for j, (item, u) in enumerate(batch)],
        )
        try:
            res: normalize_item.Output = await llm.run("normalize_item", inputs)
        except llm.LLMError as exc:
            log.info("ai.normalize_skipped", reason=str(exc)[:200])
            break
        for item in res.items:
            if not item.canonical or not item.id.isdigit() or int(item.id) >= len(keys):
                continue
            raw, _unit = keys[int(item.id)]
            # модель не добавляет чисел: диаметр, марку и размеры берёт только из исходного текста
            if not _numbers(item.canonical) <= _numbers(raw):
                log.info("ai.normalize_invented_number", raw=raw, canonical=item.canonical)
                continue
            for idx in todo[keys[int(item.id)]]:
                line = norm[idx].line
                probe = normalize_line(replace(line, item_raw=item.canonical))
                if probe.status != "ok" or (item.head and probe.head != item.head):
                    continue
                out[idx] = replace(probe, line=line, note=f"ИИ: «{item.canonical}»")
                fixed += 1
    return out, fixed


# --- проверка ответа снабжения -------------------------------------------------------------------


async def review_answer(finding_id: uuid.UUID) -> dict | None:
    """Подсказка собственнику к ответу снабжения; результат — в findings.answer_review."""
    from secondkey.engine.report import RULE_TITLES

    async with session_scope() as s:
        f = await s.get(Finding, finding_id)
        if f is None or not f.answer:
            return None
        line_ids = [fl.line_id for fl in f.lines]
        lines = (await s.scalars(select(PurchaseLine).where(PurchaseLine.id.in_(line_ids)))).all()
        facts = _line_facts(lines)
        inputs = answer_review.Inputs(
            question=f.title,
            rule=RULE_TITLES.get(f.code, f.title),
            explanation=f.message,
            overpayment=f"{_int(f.overpayment)} сом",
            supplier=f.supplier,
            reference=f"{_int(f.reference)} сом за единицу" if f.reference else None,
            facts=facts,
            answer=f.answer,
            answer_reasons=list(f.answer_reasons or []),
        )
        answered_at = f.answered_at
    try:
        out: answer_review.Output = await llm.run("answer_review", inputs)
    except llm.LLMError as exc:
        log.info("ai.review_skipped", finding=str(finding_id), reason=str(exc)[:200])
        return None
    review = {
        **out.model_dump(),
        "version": "answer_review/" + _version("answer_review"),
        "at": datetime.now(UTC).isoformat(),
    }
    async with session_scope() as s:
        f = await s.get(Finding, finding_id)
        # ответ успели дополнить — эта проверка устарела, её сделает следующая задача
        if f is None or f.answered_at != answered_at:
            return None
        f.answer_review = review
    return review


def _line_facts(lines) -> list[str]:
    if not lines:
        return []
    lines = sorted(lines, key=lambda x: x.date)
    prices = [x.price_base for x in lines if x.price_base is not None]
    qty = sum((x.qty_base or Decimal("0")) for x in lines)
    unit = lines[0].unit_base or ""
    facts = [
        f"строк: {len(lines)}, период {lines[0].date:%d.%m.%Y} — {lines[-1].date:%d.%m.%Y}",
        f"поставщики: {', '.join(sorted({x.supplier for x in lines}))}",
        f"объекты: {', '.join(sorted({x.object_name for x in lines if x.object_name})) or 'не указаны'}",
    ]
    if prices:
        facts.append(
            f"цена за единицу ({unit}): от {_int(min(prices))} до {_int(max(prices))} сом; объём {qty:.3f} {unit}"
        )
    return facts


# --- дайджест собственнику ----------------------------------------------------------------------

_DIGITS = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")


def _nums_in(text: str) -> set[str]:
    return {re.sub(r"[\s ]", "", n).rstrip(".,").replace(",", ".") for n in _DIGITS.findall(text)}


def numbers_grounded(text: str, facts: str) -> bool:
    """Каждое число в тексте модели — из фактов (номера пунктов 1–9 разрешены)."""
    allowed = _nums_in(facts) | {str(i) for i in range(1, 10)}
    return _nums_in(text) <= allowed


async def owner_digest_text(inputs: owner_digest.Inputs) -> tuple[str, str]:
    """Текст для WhatsApp и кто его написал: «ai» или «template»."""
    template = render("owner_digest", inputs, template="fallback.j2").user
    if not llm.enabled():
        return template, "template"
    try:
        out: owner_digest.Output = await llm.run("owner_digest", inputs)
    except llm.LLMError:
        return template, "template"
    facts = render("owner_digest", inputs).user
    if not numbers_grounded(out.text, facts):
        log.warning("ai.digest_ungrounded_numbers")
        return template, "template"
    return out.text.strip(), "ai"


def _int(v) -> str:
    return f"{Decimal(v):,.0f}".replace(",", " ") if v is not None else "—"


def _version(module: str) -> str:
    from secondkey.prompts import get

    return get(module).spec.version
