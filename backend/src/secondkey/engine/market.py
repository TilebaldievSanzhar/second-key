"""Рыночный эталон цен: точки «позиция × дата × цена за базовую единицу».

Источники: построчные цены госзакупок КР (planned price и ставки участников,
см. secondkey.market.kg_goszakupki), позже — обезличенные данные клиентов и индекс.
Точка привязывается к позиции тем же нормализатором, что и строки клиента, —
поэтому сравнение идёт «позиция с позицией», а не «текст с текстом».
"""

from __future__ import annotations

import bisect
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


@dataclass
class MarketPoint:
    sku: str
    date: date
    price: Decimal  # за базовую единицу позиции
    unit: str
    source: str  # kg_goszakupki:plan | kg_goszakupki:bid | synthetic | client
    region: str | None = None
    ref: str | None = None  # номер закупки / документа — для доказательства
    label: str | None = None


class MarketStore:
    def __init__(self, points: list[MarketPoint]):
        self._by_sku: dict[str, list[MarketPoint]] = {}
        for p in points:
            self._by_sku.setdefault(p.sku, []).append(p)
        for v in self._by_sku.values():
            v.sort(key=lambda p: p.date)
        self._dates = {k: [p.date for p in v] for k, v in self._by_sku.items()}

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_sku.values())

    @property
    def skus(self) -> set[str]:
        return set(self._by_sku)

    def points(self, sku: str, on: date, window_days: int) -> list[MarketPoint]:
        pts = self._by_sku.get(sku)
        if not pts:
            return []
        ds = self._dates[sku]
        lo = bisect.bisect_left(ds, on - timedelta(days=window_days))
        hi = bisect.bisect_right(ds, on + timedelta(days=window_days))
        return pts[lo:hi]

    def lookup(self, sku: str, on: date, window_days: int) -> list[Decimal]:
        return [p.price for p in self.points(sku, on, window_days)]

    # ------------------------------------------------------------------ io
    def save_jsonl(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for v in self._by_sku.values():
                for p in v:
                    d = asdict(p)
                    d["date"] = p.date.isoformat()
                    d["price"] = str(p.price)
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")

    @classmethod
    def load_jsonl(cls, *paths: str | Path) -> MarketStore:
        points = []
        for path in paths:
            path = Path(path)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                d["date"] = date.fromisoformat(d["date"])
                d["price"] = Decimal(d["price"])
                points.append(MarketPoint(**d))
        return cls(points)
