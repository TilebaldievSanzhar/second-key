# Контракт строк закупок

`POST {адрес}/api/integrations/purchases` с `Authorization: Bearer sk_…` (право `push`) или MCP-инструмент
`submit_purchases` — один и тот же формат. Источник истины — `backend/src/secondkey/integrations/contract.py`.

```json
{
  "source": "1c:bp30",
  "period_start": "2025-10-01",
  "period_end": "2026-09-30",
  "lines": [
    {
      "date": "2026-01-17",
      "doc": "Поступление товаров и услуг 0002388 от 17.01.2026",
      "supplier": "ИП Сыдыков А.Т.",
      "supplier_inn": "20909197900124",
      "item": "Арматура А500С ф14",
      "unit": "т",
      "qty": "4.246",
      "price": "82538.19",
      "amount": "350457.15",
      "vat": "37549.08",
      "object": "ЖК «Ордо», Бишкек",
      "responsible": "Асанов Б.",
      "ref": "GUID документа"
    }
  ]
}
```

| Поле | Обязательно | Что |
|---|---|---|
| date | да | дата документа, ГГГГ-ММ-ДД |
| supplier | да | наименование контрагента |
| item | да | номенклатура как в 1С, без правок |
| qty | да | количество в единице документа, > 0 |
| price / amount | хотя бы одно | цена за единицу; сумма строки с НДС |
| doc, supplier_inn, unit, vat, object, responsible, ref | нет | улучшают доказательства и разрезы |

Числа — числом или строкой, дробная часть через точку.
Ответ: `202 {"run_id": "…", "status": "queued", "lines": N, "status_url": "…/api/audits/{run_id}"}`.
Ошибка формата — `422` со списком полей.
