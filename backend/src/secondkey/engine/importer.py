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

from secondkey.engine.inn import clean_inn
from secondkey.engine.models import PurchaseLine, SupplierCard
from secondkey.engine.numbers import parse_decimal
from secondkey.engine.payments import Payment

# поле → синонимы заголовка (нижний регистр); порядок полей важен:
# «сумма ндс» должна уйти в vat раньше, чем «сумма» заберёт её в amount
# входящие номер и дата (счёт/накладная поставщика) — раньше «номера» и «даты» документа;
# реквизиты контрагента — только с «контрагента/поставщика» в заголовке, чтобы не забрать «адрес доставки»
FIELD_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("vat", ("сумма ндс", "в т.ч. ндс", "в том числе ндс", "ндс")),
    ("supplier_inn", ("инн контрагента", "инн поставщика", "инн")),
    (
        "doc_in_no",
        ("номер входящего документа", "входящий номер", "вх. номер", "номер вх.", "вх. №", "№ вх.", "вх.номер"),
    ),
    ("doc_in_date", ("дата входящего документа", "входящая дата", "дата вх.", "вх. дата", "дата вх")),
    ("date", ("дата документа", "дата поступления", "дата", "период")),
    ("doc_no", ("номер документа", "№ документа", "номер", "документ", "регистратор")),
    ("supplier_phone", ("телефон контрагента", "телефон поставщика")),
    ("supplier_email", ("email контрагента", "e-mail контрагента", "эл. почта контрагента", "email поставщика")),
    ("supplier_address", ("адрес контрагента", "адрес поставщика", "юридический адрес контрагента")),
    (
        "supplier_account",
        ("расчетный счет контрагента", "расчётный счёт контрагента", "р/с контрагента", "счет контрагента"),
    ),
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

# оплаты поставщикам — отдельный лист книги («Оплаты», «Платежи», «Списания»): связкам нужны авансы и сроки оплат
PAYMENT_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("supplier_inn", ("инн контрагента", "инн получателя", "инн поставщика", "инн")),
    ("date", ("дата платежа", "дата оплаты", "дата документа", "дата")),
    ("doc_no", ("номер документа", "номер платежа", "документ", "номер")),
    ("supplier", ("контрагент", "получатель", "поставщик")),
    ("amount", ("сумма платежа", "сумма оплаты", "сумма документа", "сумма")),
    ("purpose", ("назначение платежа", "назначение")),
]
PAYMENT_REQUIRED = ("date", "supplier", "amount")
PAYMENT_SHEET = re.compile(r"оплат|плат[её]ж|списан", re.I)


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
    suppliers: dict[str, SupplierCard] = field(default_factory=dict)  # имя поставщика → реквизиты из колонок
    payments: list[Payment] = field(default_factory=list)  # оплаты поставщикам — с листа «Оплаты», если он есть


def _norm_header(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def map_columns(
    header: list[object], synonyms_by_field: list[tuple[str, tuple[str, ...]]] | None = None
) -> dict[str, int]:
    """Поле → индекс колонки. Точное совпадение важнее вхождения; колонка — одному полю."""
    cells = [_norm_header(h) for h in header]
    used: set[int] = set()
    mapping: dict[str, int] = {}
    for fld, synonyms in synonyms_by_field or FIELD_SYNONYMS:
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


def _read_book(path: Path) -> list[tuple[str, list[list[object]]]]:
    """Листы книги (название, строки); у CSV — один безымянный лист."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
        finally:
            wb.close()  # read_only держит файл открытым: на Windows временный файл иначе не удалить
    return [("", _read_csv(path))]


def _read_rows(path: Path) -> list[list[object]]:
    return _read_book(path)[0][1]


def _read_csv(path: Path) -> list[list[object]]:
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
    book = _read_book(path)
    rows = book[0][1]
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
    result = _build(rows[hdr_idx + 1 :], mapping, header_row=hdr_idx + 1, first_row_no=hdr_idx + 2)
    for title, sheet in book[1:]:
        if PAYMENT_SHEET.search(title) or any(
            "назначение платежа" in _norm_header(c) for r in sheet[:25] for c in r if c is not None
        ):
            result.payments += payments_from_rows(sheet, title, result.warnings)
    return result


def payments_from_rows(rows: list[list[object]], sheet: str, warnings: list[str]) -> list[Payment]:
    """Оплаты поставщикам с листа книги: шапка — первая строка, где нашлись дата, получатель и сумма."""
    header = next(
        (
            i
            for i, row in enumerate(rows[:25])
            if all(f in map_columns(row, PAYMENT_SYNONYMS) for f in PAYMENT_REQUIRED)
        ),
        None,
    )
    if header is None:
        warnings.append(f"лист «{sheet}»: не нашли шапку оплат (дата, контрагент, сумма) — оплаты не учтены")
        return []
    mapping = map_columns(rows[header], PAYMENT_SYNONYMS)

    def cell(r: list[object], fld: str) -> object:
        idx = mapping.get(fld)
        return r[idx] if idx is not None and idx < len(r) else None

    out = []
    for j, r in enumerate(rows[header + 1 :], start=header + 2):
        d = parse_date(cell(r, "date"))
        supplier = re.sub(r"\s+", " ", str(cell(r, "supplier") or "")).strip()
        amount = parse_number(cell(r, "amount"))
        if d is None or not supplier or amount is None or amount <= 0:
            continue
        out.append(
            Payment(
                date=d,
                supplier=supplier,
                amount=amount,
                supplier_inn=clean_inn(cell(r, "supplier_inn")),
                doc_no=str(cell(r, "doc_no")).strip() if cell(r, "doc_no") not in (None, "") else None,
                purpose=str(cell(r, "purpose")).strip() if cell(r, "purpose") not in (None, "") else None,
                payment_id=f"p{j}",
            )
        )
    return out


def import_payments(records: list[dict[str, object]]) -> list[Payment]:
    """Оплаты из JSON-выгрузки 1С (поле payments контракта)."""
    out = []
    for i, r in enumerate(records, start=1):
        d = parse_date(r.get("date"))
        amount = parse_number(r.get("amount"))
        supplier = re.sub(r"\s+", " ", str(r.get("supplier") or "")).strip()
        if d is None or not supplier or amount is None or amount <= 0:
            continue
        out.append(
            Payment(
                date=d,
                supplier=supplier,
                amount=amount,
                supplier_inn=clean_inn(r.get("supplier_inn")),
                doc_no=str(r["doc"]).strip() if r.get("doc") else None,
                purpose=str(r["purpose"]).strip() if r.get("purpose") else None,
                payment_id=f"p{i}",
            )
        )
    return out


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
    "doc_in_no": "doc_in_no",
    "doc_in_date": "doc_in_date",
}

CARD_FIELDS = {
    "supplier_phone": "phone",
    "supplier_email": "email",
    "supplier_address": "address",
    "supplier_account": "bank_account",
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

    def text(row: list[object], fld: str) -> str | None:
        v = cell(row, fld)
        if v in (None, ""):
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)  # «41.0» из Excel → «41»
        return str(v).strip() or None

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
        doc_in_no = text(row, "doc_in_no")
        doc_no = text(row, "doc_no") or doc_in_no  # в таблице только входящий номер — он и номер документа
        supplier = re.sub(r"\s+", " ", supplier)
        card_values = {attr: text(row, fld) for fld, attr in CARD_FIELDS.items()}
        if any(card_values.values()):
            card = result.suppliers.setdefault(supplier, SupplierCard(name=supplier))
            for attr, v in card_values.items():
                if v and not getattr(card, attr):
                    setattr(card, attr, v)
        result.lines.append(
            PurchaseLine(
                line_id=f"r{i}",
                date=d,
                supplier=supplier,
                item_raw=item,
                qty=qty,
                unit_raw=(str(cell(row, "unit")).strip() if cell(row, "unit") not in (None, "") else None),
                price=price,
                amount=amount,
                doc_no=doc_no,
                supplier_inn=clean_inn(cell(row, "supplier_inn")) or text(row, "supplier_inn"),
                vat_included=(True if vat and vat > 0 else None),
                object_name=(str(cell(row, "object_name")).strip() if cell(row, "object_name") else None),
                responsible=(str(cell(row, "responsible")).strip() if cell(row, "responsible") else None),
                source_row=i,
                doc_in_no=doc_in_no,
                doc_in_date=parse_date(cell(row, "doc_in_date")),
            )
        )
    for card in result.suppliers.values():
        card.inn = next((L.supplier_inn for L in result.lines if L.supplier == card.name and L.supplier_inn), None)
    return result
