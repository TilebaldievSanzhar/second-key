// Цены позиции во времени: покупки по поставщикам, строки вопроса в кольце, рынок — полые квадраты
// и полоса «медиана ± допуск», эталон — пунктир. Подсказка на наведение и касание; таблица строк
// под графиком — его текстовая версия.
//
// Палитра поставщиков проверена validate_palette.js (--pairs all, светлая поверхность #FDFDFB):
// янтарь — поставщик вопроса, затем синий и бирюзовый; остальные — серым «Другие поставщики».
import { useEffect, useMemo, useRef, useState } from "react";
import type { PriceHistory, PricePoint } from "../api/client";
import { MONTHS, date, docNumber, int, unit as unitText } from "../format";

const SLOT = ["#256abf", "#199e70"];
const QUESTION = "#9A5B00";
const OTHER = "#8a8f8b";
const MARKET = "#6B6F6C";

type Mark = PricePoint & { x: number; y: number; color: string; kind: "buy" | "market"; ring: boolean };

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
};

function niceStep(span: number, target: number) {
  const raw = span / target;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? 10 * p;
}

const ts = (d: string) => new Date(`${d}T00:00:00`).getTime();
const tickLabel = (v: number, big: boolean) =>
  big ? `${(v / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} тыс` : int(v);

const MARKET_SOURCE: Record<string, string> = {
  "kg_goszakupki:plan": "госзакупки КР, план",
  "kg_goszakupki:bid": "госзакупки КР, ставка",
};
const sourceText = (s: string) => MARKET_SOURCE[s] ?? (s.startsWith("synthetic") ? "госзакупки (демо)" : s);

export function PriceChart({
  history,
  supplier,
  lineIds,
  reference,
  tolerance,
  height = 220,
}: {
  history: PriceHistory;
  supplier: string | null | undefined;
  lineIds: Set<string>;
  reference: number | null;
  tolerance: number;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [active, setActive] = useState<Mark | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // цвет следует за поставщиком, а не за рангом: порядок — по числу покупок, стабилен для позиции
  const colors = useMemo(() => {
    const n = new Map<string, number>();
    for (const p of history.purchases) if (p.supplier && p.supplier !== supplier) n.set(p.supplier, (n.get(p.supplier) ?? 0) + 1);
    const order = [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([s]) => s);
    const map = new Map<string, string>();
    if (supplier) map.set(supplier, QUESTION);
    order.forEach((s, i) => map.set(s, SLOT[i] ?? OTHER));
    const plain = history.purchases.some((p) => p.supplier === supplier && !(p.line_id && lineIds.has(p.line_id)));
    return { map, named: order.slice(0, SLOT.length), hasOther: order.length > SLOT.length, plain };
  }, [history.purchases, supplier, lineIds]);

  const L = 52, R = 12, T = 10, B = 26;
  const pw = width - L - R;
  const ph = height - T - B;

  const geo = useMemo(() => {
    const all = [...history.purchases, ...history.market];
    const prices = all.map((p) => Number(p.price));
    const mk = history.market.map((p) => Number(p.price));
    const med = mk.length ? median(mk) : null;
    const band = med !== null ? [med * (1 - tolerance), med * (1 + tolerance)] : null;
    const ys = [...prices, ...(band ?? []), ...(reference ? [reference] : [])];
    let lo = Math.min(...ys);
    let hi = Math.max(...ys);
    const pad = (hi - lo || hi * 0.1) * 0.08;
    lo -= pad;
    hi += pad;
    const step = niceStep(hi - lo, height < 240 ? 4 : 5);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const ticks: number[] = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);

    const dates = all.map((p) => ts(p.date));
    const a = new Date(Math.min(...dates));
    const b = new Date(Math.max(...dates));
    const d0 = new Date(a.getFullYear(), a.getMonth(), 1).getTime();
    const d1 = new Date(b.getFullYear(), b.getMonth() + 1, 1).getTime();
    const months: { t: number; m: number }[] = [];
    for (let d = new Date(d0); d.getTime() < d1; d.setMonth(d.getMonth() + 1)) months.push({ t: d.getTime(), m: d.getMonth() });
    return { lo, hi, ticks, d0, d1, months, med, band, big: hi >= 10000 };
  }, [history, reference, tolerance, height]);

  const X = (t: number) => L + ((t - geo.d0) / (geo.d1 - geo.d0 || 1)) * pw;
  const Y = (v: number) => T + (1 - (v - geo.lo) / (geo.hi - geo.lo || 1)) * ph;

  // точек — десятки: пересчёт на каждый рендер дешевле мемоизации с замыканиями X/Y
  const marks: Mark[] = (() => {
    const m: Mark[] = history.market.map((p) => ({ ...p, x: X(ts(p.date)), y: Y(Number(p.price)), color: MARKET, kind: "market", ring: false }));
    const buys = history.purchases.map((p) => ({
      ...p,
      x: X(ts(p.date)),
      y: Y(Number(p.price)),
      color: colors.map.get(p.supplier ?? "") ?? OTHER,
      kind: "buy" as const,
      ring: !!p.line_id && lineIds.has(p.line_id),
    }));
    // поставщик вопроса и строки вопроса — поверх остальных
    buys.sort((a, b) => Number(a.color === QUESTION) - Number(b.color === QUESTION) || Number(a.ring) - Number(b.ring));
    return [...m, ...buys];
  })();

  const step = pw / geo.months.length < 44 ? 2 : 1;
  const u = unitText(history.unit);
  const qBuys = history.purchases.filter((p) => p.supplier === supplier).map((p) => Number(p.price));
  const others = history.purchases.filter((p) => p.supplier !== supplier).map((p) => Number(p.price));
  const range = (xs: number[]) => (xs.length ? `от ${int(Math.min(...xs))} до ${int(Math.max(...xs))}` : "нет данных");
  const summary = `Цены «${history.label}», сом за ${u}: у ${supplier ?? "поставщика вопроса"} ${range(qBuys)}, у других поставщиков ${range(others)}${geo.med ? `, рынок около ${int(geo.med)}` : ""}.`;

  const tipLeft = active ? Math.min(Math.max(active.x + 14, 0), width - 248) : 0;
  const flip = active ? active.x + 14 + 236 > width : false;

  return (
    <figure className="chart">
      <div className="chart-plot" ref={box} onPointerLeave={() => setActive(null)}>
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary}>
          {geo.band && (
            <rect x={L} y={Y(geo.band[1]!)} width={pw} height={Y(geo.band[0]!) - Y(geo.band[1]!)} className="ch-band" />
          )}
          {geo.ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={width - R} y1={Y(v)} y2={Y(v)} className="ch-grid" />
              <text x={L - 8} y={Y(v) + 4} textAnchor="end" className="ch-tick">
                {tickLabel(v, geo.big)}
              </text>
            </g>
          ))}
          {geo.months.map((m, i) =>
            i % step ? null : (
              <text key={m.t} x={X(m.t) + 2} y={height - 7} className="ch-tick">
                {MONTHS[m.m]}
              </text>
            ),
          )}
          {geo.med !== null && <line x1={L} x2={width - R} y1={Y(geo.med)} y2={Y(geo.med)} className="ch-median" />}
          {reference !== null && (
            <g>
              <line x1={L} x2={width - R} y1={Y(reference)} y2={Y(reference)} className="ch-ref" />
              <text x={width - R - 2} y={Y(reference) + 14} textAnchor="end" className="ch-ref-text">
                эталон {int(reference)}
              </text>
            </g>
          )}
          {active && <line x1={active.x} x2={active.x} y1={active.y + 8} y2={T + ph} className="ch-cross" />}
          {marks.map((p, i) =>
            p.kind === "market" ? (
              <rect key={`m${i}`} x={p.x - 3.5} y={p.y - 3.5} width={7} height={7} className="ch-market" />
            ) : (
              <g key={`b${i}`}>
                <circle cx={p.x} cy={p.y} r={4} fill={p.color} className="ch-dot" />
                {p.ring && <circle cx={p.x} cy={p.y} r={7.5} className="ch-ring" />}
              </g>
            ),
          )}
          {/* зона касания больше точки: 24 px */}
          {marks.map((p, i) => (
            <circle
              key={`h${i}`}
              cx={p.x}
              cy={p.y}
              r={12}
              className="ch-hit"
              onPointerEnter={() => setActive(p)}
              onClick={() => setActive(p)}
            />
          ))}
        </svg>
        {active && (
          <div className={`ch-tip${flip ? " ch-tip-flip" : ""}`} style={{ left: flip ? Math.max(active.x - 250, 0) : tipLeft, top: Math.max(active.y - 66, 0) }}>
            <strong>
              {date(active.date)} · {int(active.price)} сом/{u}
            </strong>
            <span>{active.kind === "market" ? `Рынок · ${sourceText(active.source)}` : active.supplier}</span>
            {active.ref && <span className="mono">{active.kind === "market" ? `закупка ${active.ref}` : `Поступление ${docNumber(active.ref)}`}</span>}
          </div>
        )}
      </div>
      <figcaption className="legend">
        {supplier && (
          <span>
            <i className="lg-ring" /> {supplier} · строки вопроса
          </span>
        )}
        {supplier && colors.plain && (
          <span>
            <i className="lg-dot" style={{ background: QUESTION }} /> {supplier}
          </span>
        )}
        {colors.named.map((s, i) => (
          <span key={s}>
            <i className="lg-dot" style={{ background: SLOT[i] }} /> {s}
          </span>
        ))}
        {colors.hasOther && (
          <span>
            <i className="lg-dot" style={{ background: OTHER }} /> Другие поставщики
          </span>
        )}
        {geo.med !== null && (
          <span>
            <i className="lg-square" /> Рынок, медиана ± {Math.round(tolerance * 100)}%
          </span>
        )}
      </figcaption>
    </figure>
  );
}
