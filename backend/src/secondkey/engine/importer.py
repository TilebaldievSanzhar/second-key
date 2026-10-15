"""Импорт реестра закупок из Excel/CSV (выгрузка 1С и самодельные таблицы).

Шапку ищем сами: в выгрузках 1С над таблицей идут строки отчёта («Закупки за
период…», параметры отбора). Колонки сопоставляются по словарю синонимов; числа
и даты разбираются детерминированно. Строка без даты, поставщика, номенклатуры
или количества не угадывается — она попадает в предупреждения.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from secondkey.engine.models import PurchaseLine
from secondkey.engine.numbers import parse_decimal

# поле → синонимы заголовка (нижний регистр); порядок полей важен:
# «сумма ндс» должна уйти в vat раньше, чем «сумма» заберёт её в amount
FIELD_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("vat", ("сумма ндс", "в т.ч. ндс", "в том числе ндс", "ндс")),
    ("supplier_inn", ("инн контрагента", "инн поставщика", "инн")),
    ("date", ("дата документа", "дата поступления", "дата", "период")),
    ("doc_no", ("номер документа", "№ документа", "номер вх.", "вх. номер", "номер", "документ", "регистратор")),
    ("supplier", ("контрагент", "поставщик", "продавец")),
    ("item", ("номенклатура", "наименование товара", "наименование", "товар", "материал")),
    ("qty", ("количество", "кол-во", "кол.")),
    ("unit", ("единица измерения", "ед. изм.", "ед.изм.", "ед изм", "единица", "ед.")),
    ("price", ("цена с ндс", "цена за ед.", "цена")),
    ("amount", ("сумма с ндс", "сумма", "всего", "стоимость")),
    ("object_name", ("объект строительства", "объект", "склад", "подразделение")),
    ("responsible", ("ответственный", "снабженец", "менеджер", "автор")),
]
REQUIRED = ("date", "supplier", "item", "qty")


def missing_fields(mapping: dict[str, int]) -> list[str]:
    """Обязательные поля без колонки; цена считается и из суммы, поэтому нужна хотя бы одна из двух."""
    missing = [f for f in REQUIRED if f not in mapping]
    if "price" not in mapping and "amount" not in mapping:
        missing.append("price")
    return missing


_DATE_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})|(\d{4})-(\d{2})-(\d{2})")


@dataclass
class ImportResult:
    lines: list[PurchaseLine]
    mapping: dict[str, int]  # поле → индекс колонки
    header_row: int  # номер строки шапки (1-based)
    warnings: list[str] = field(default_factory=list)


def _norm_header(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def map_columns(header: list[object]) -> dict[str, int]:
    """Поле → индекс колонки. Точное совпадение важнее вхождения; колонка — одному полю."""
    cells = [_norm_header(h) for h in header]
    used: set[int] = set()
    mapping: dict[str, int] = {}
    for fld, synonyms in FIELD_SYNONYMS:
        best: tuple[int, int] | None = None  # (ранг, индекс): меньше — лучше
        for idx, cell in enumerate(cells):
            if idx in used or not cell:
                continue
            for rank, syn in enumerate(synonyms):
                if cell == syn:
                    score = rank
                elif cell.startswith(syn):
                    score = 100 + rank
                elif syn in cell and len(syn) > 3:
                    score = 200 + rank
                else:
                    continue
                if best is None or score < best[0]:
                    best = (score, idx)
        if best is not None:
            mapping[fld] = best[1]
            used.add(best[1])
    return mapping


def parse_date(v: object) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:  # серийная дата Excel
        return date(1899, 12, 30) + timedelta(days=int(v))
    m = _DATE_RE.search(str(v or ""))  # «Поступление товаров 000123 от 12.03.2026 10:15:00»
    if not m:
        return None
    if m.group(4):
        y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    else:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_number(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(round(float(v), 6))).normalize()
    return parse_decimal(str(v))


def _read_rows(path: Path) -> list[list[object]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()  # read_only держит файл открытым: на Windows временный файл иначе не удалить
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def find_header(rows: list[list[object]], scan: int = 25) -> tuple[int, dict[str, int]] | None:
    """Первая строка, где нашлись все обязательные поля."""
    for i, row in enumerate(rows[:scan]):
        mapping = map_columns(row)
        if all(f in mapping for f in REQUIRED):
            return i, mapping
    return None


@dataclass
class Preview:
    """Что нашли в файле до импорта: шапка, сопоставление колонок, первые строки."""

    header_row: int  # 1-based
    columns: list[str]
    mapping: dict[str, int]
    sample: list[list[str]]
    missing: list[str]  # обязательные поля, которые не нашлись
    data_rows: int = 0  # непустых строк под шапкой


def preview_registry(path: str | Path, sample_rows: int = 8) -> Preview:
    rows = _read_rows(Path(path))
    found = find_header(rows)
    if found is None:
        # шапку не нашли: покажем первые строки, сопоставление сделает человек
        return Preview(
            header_row=0,
            columns=[],
            mapping={},
            missing=missing_fields({}),
            sample=[[_cell_str(c) for c in r] for r in rows[:sample_rows]],
            data_rows=sum(1 for r in rows if any(r)),
        )
    idx, mapping = found
    return Preview(
        header_row=idx + 1,
        columns=[_cell_str(c) for c in rows[idx]],
        mapping=mapping,
        sample=[[_cell_str(c) for c in r] for r in rows[idx + 1 : idx + 1 + sample_rows] if any(r)],
        missing=missing_fields(mapping),
        data_rows=sum(1 for r in rows[idx + 1 :] if any(r)),
    )


def _cell_str(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    return str(v)


def import_registry(
    path: str | Path, mapping: dict[str, int] | None = None, header_row: int | None = None
) -> ImportResult:
    """Импорт реестра. mapping/header_row — подтверждённое человеком сопоставление (иначе автопоиск)."""
    path = Path(path)
    rows = _read_rows(path)
    if mapping is not None and header_row:
        missing = missing_fields(mapping)
        if missing:
            raise ValueError(f"в сопоставлении нет обязательных полей: {', '.join(missing)}")
        hdr_idx = header_row - 1
    else:
        found = find_header(rows)
        if found is None:
            raise ValueError(
                "не найдена шапка таблицы: нужны колонки «Дата», «Контрагент/Поставщик», "
                "«Номенклатура/Наименование», «Количество»"
            )
        hdr_idx, mapping = found
    return _build(rows[hdr_idx + 1 :], mapping, header_row=hdr_idx + 1, first_row_no=hdr_idx + 2)


# поля контракта JSON-выгрузки (интеграции с 1С) → поля движка
RECORD_FIELDS = {
    "date": "date",
    "doc": "doc_no",
    "supplier": "supplier",
    "supplier_inn": "supplier_inn",
    "item": "item",
    "unit": "unit",
    "qty": "qty",
    "price": "price",
    "amount": "amount",
    "vat": "vat",
    "object": "object_name",
    "responsible": "responsible",
}


def import_records(records: list[dict[str, object]]) -> ImportResult:
    """Импорт строк из JSON (выгрузка из 1С по API): те же проверки, что у таблицы; номер строки — номер записи."""
    keys = list(RECORD_FIELDS)
    rows = [[r.get(k) for k in keys] for r in records]
    mapping = {RECORD_FIELDS[k]: i for i, k in enumerate(keys)}
    return _build(rows, mapping, header_row=0, first_row_no=1)


def _build(rows: list[list[object]], mapping: dict[str, int], header_row: int, first_row_no: int) -> ImportResult:
    result = ImportResult(lines=[], mapping=mapping, header_row=header_row)

    def cell(row: list[object], fld: str) -> object:
        idx = mapping.get(fld)
        return row[idx] if idx is not None and idx < len(row) else None

    for i, row in enumerate(rows, start=first_row_no):
        if not any(c not in (None, "") for c in row):
            continue
        d = parse_date(cell(row, "date")) or parse_date(cell(row, "doc_no"))
        supplier = str(cell(row, "supplier") or "").strip()
        item = str(cell(row, "item") or "").strip()
        qty = parse_number(cell(row, "qty"))
        if not supplier and not item:
            continue  # итоговые строки, разделители групп
        if d is None or not supplier or not item or qty is None or qty == 0:
            result.warnings.append(f"строка {i}: пропущена (нет даты, поставщика, номенклатуры или количества)")
            continue
        price = parse_number(cell(row, "price"))
        amount = parse_number(cell(row, "amount"))
        if price is None and amount is None:
            result.warnings.append(f"строка {i}: нет ни цены, ни суммы — пропущена")
            continue
        if price is None:
            price = (amount / qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if amount is None:
            amount = (price * qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        elif abs(price * qty - amount) > max(Decimal("1"), abs(amount) * Decimal("0.01")):
            result.warnings.append(
                f"строка {i}: цена × количество ({price * qty:.2f}) не равно сумме ({amount}); "
                "цена пересчитана из суммы"
            )
            price = (amount / qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        vat = parse_number(cell(row, "vat"))
        doc_no = cell(row, "doc_no")
        result.lines.append(
            PurchaseLine(
                line_id=f"r{i}",
                date=d,
                supplier=re.sub(r"\s+", " ", supplier),
                item_raw=item,
                qty=qty,
                unit_raw=(str(cell(row, "unit")).strip() if cell(row, "unit") not in (None, "") else None),
                price=price,
                amount=amount,
                doc_no=str(doc_no).strip() if doc_no not in (None, "") else None,
                supplier_inn=(str(cell(row, "supplier_inn")).strip() if cell(row, "supplier_inn") else None),
                vat_included=(True if vat and vat > 0 else None),
                object_name=(str(cell(row, "object_name")).strip() if cell(row, "object_name") else None),
                responsible=(str(cell(row, "responsible")).strip() if cell(row, "responsible") else None),
                source_row=i,
            )
        )
    return result
