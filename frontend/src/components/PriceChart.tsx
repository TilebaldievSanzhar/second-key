// Цены позиции во времени.
//   линии — покупки по поставщикам (поставщик вопроса — янтарь, сверху), кольца — строки этого вопроса;
//   отрезок от эталона до цены — переплата на единицу у строк вопроса;
//   полоса — рынок по месяцам: медиана ± допуск, квадраты — сами рыночные точки;
//   перекрестие находит ближайшую дату, подсказка показывает все ряды на эту дату (мышь, касание, стрелки).
// Палитра — токены --chart-* (styles.css), проверены validate_palette.js для светлой (#FDFDFB) и тёмной (#1C1F1D)
// поверхностей (--pairs all): янтарь — поставщик вопроса, синий, бирюзовый;
// остальные поставщики — серые «Другие» без линии. Таблица строк под графиком — его текстовая версия.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { PriceHistory, PricePoint } from "../api/client";
import { MONTHS, date, docNumber, int, qty, unit as unitText } from "../format";
import { chartHeight, useFullscreen } from "./chartFrame";
import { Icon } from "./Icon";

const SLOT = ["var(--chart-1)", "var(--chart-2)"];
const QUESTION = "var(--chart-q)";
const OTHER = "var(--chart-other)";
const DAY = 86_400_000;

type Series = { name: string; color: string; question: boolean; line: boolean; points: (PricePoint & { t: number })[] };
type Range = "all" | "question";

const ts = (d: string) => new Date(`${d}T00:00:00`).getTime();
const monthKey = (t: number) => {
  const d = new Date(t);
  return `${d.getFullYear()}-${d.getMonth()}`;
};
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
const tick = (v: number, big: boolean) => (big ? `${(v / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} тыс` : int(v));
const short = (s: string) => s.replace(/^(ОсОО|ОАО|ЗАО|ООО|ИП)\s+/u, "").replace(/[«»"]/g, "");

export function PriceChart({
  history,
  supplier,
  lineIds,
  reference,
  tolerance,
  height: heightProp,
  title,
}: {
  history: PriceHistory;
  supplier: string | null | undefined;
  lineIds: Set<string>;
  reference: number | null;
  tolerance: number;
  height?: number;
  title?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null); // индекс даты в dates
  const [range, setRange] = useState<Range>("all");
  const [zoom, setZoom] = useState<[number, number] | null>(null); // выделенный мышью период
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null);
  const { full, toggle, viewH } = useFullscreen();

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ряды: цвет следует за поставщиком (порядок — по числу покупок), а не за рангом на экране
  const series = useMemo<Series[]>(() => {
    const by = new Map<string, (PricePoint & { t: number })[]>();
    for (const p of history.purchases) {
      const name = p.supplier ?? "—";
      by.set(name, [...(by.get(name) ?? []), { ...p, t: ts(p.date) }]);
    }
    const others = [...by.keys()].filter((n) => n !== supplier).sort((a, b) => by.get(b)!.length - by.get(a)!.length || a.localeCompare(b));
    const out: Series[] = others.map((n, i) => ({
      name: n,
      color: SLOT[i] ?? OTHER,
      question: false,
      line: i < SLOT.length,
      points: by.get(n)!.sort((a, b) => a.t - b.t),
    }));
    if (supplier && by.has(supplier)) out.push({ name: supplier, color: QUESTION, question: true, line: true, points: by.get(supplier)!.sort((a, b) => a.t - b.t) });
    return out;
  }, [history.purchases, supplier]);

  const market = useMemo(() => {
    const pts = history.market.map((p) => ({ ...p, t: ts(p.date) }));
    const months = new Map<string, number[]>();
    for (const p of pts) months.set(monthKey(p.t), [...(months.get(monthKey(p.t)) ?? []), Number(p.price)]);
    const steps = [...months.entries()]
      .map(([k, xs]) => {
        const [y, m] = k.split("-").map(Number) as [number, number];
        return { t: new Date(y, m, 15).getTime(), med: median(xs), k };
      })
      .sort((a, b) => a.t - b.t);
    return { pts, steps, byMonth: new Map(steps.map((s) => [s.k, s.med])) };
  }, [history.market]);

  const qPoints = series.find((s) => s.question)?.points.filter((p) => p.line_id && lineIds.has(p.line_id)) ?? [];
  const hasQuestionRange = qPoints.length > 0;

  // домен: весь период или период строк вопроса ± месяц
  const all = [...series.flatMap((s) => s.points), ...market.pts];
  const [t0, t1] = zoom ?? (() => {
    const src = range === "question" && hasQuestionRange ? qPoints : all;
    const lo = Math.min(...src.map((p) => p.t)) - (range === "question" ? 31 * DAY : 0);
    const hi = Math.max(...src.map((p) => p.t)) + (range === "question" ? 31 * DAY : 0);
    const a = new Date(lo);
    const b = new Date(hi);
    return [new Date(a.getFullYear(), a.getMonth(), 1).getTime(), new Date(b.getFullYear(), b.getMonth() + 1, 1).getTime()];
  })();
  const visible = (t: number) => t >= t0 && t < t1;

  // высота — от ширины: на широком экране график не превращается в полоску; на весь экран — по окну
  const height = full ? Math.max(320, viewH - 200) : (heightProp ?? chartHeight(width, 0.36, 240, 460));
  const endLabels = width >= 560;
  const L = 52, R = endLabels ? 112 : 12, T = 12, B = 26;
  const pw = width - L - R;
  const ph = height - T - B;

  const ys = [
    ...all.filter((p) => visible(p.t)).map((p) => Number(p.price)),
    ...market.steps.filter((s) => visible(s.t)).flatMap((s) => [s.med * (1 - tolerance), s.med * (1 + tolerance)]),
    ...(reference ? [reference] : []),
  ];
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  const pad = (hi - lo || hi * 0.1) * 0.08;
  const step = niceStep(hi - lo + 2 * pad, height < 240 ? 4 : 5);
  lo = Math.floor((lo - pad) / step) * step;
  hi = Math.ceil((hi + pad) / step) * step;
  const yTicks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) yTicks.push(v);
  const big = hi >= 10000;

  const X = (t: number) => L + ((t - t0) / (t1 - t0 || 1)) * pw;
  const Y = (v: number) => T + (1 - (v - lo) / (hi - lo || 1)) * ph;

  // подписи оси: месяцы, а на коротком (приближенном) периоде — недели
  const xTicks: { t: number; label: string }[] = [];
  if (t1 - t0 <= 75 * DAY) {
    const d = new Date(t0);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() + ((8 - d.getDay()) % 7)); // ближайший понедельник
    for (; d.getTime() < t1; d.setDate(d.getDate() + 7))
      xTicks.push({ t: d.getTime(), label: `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}` });
  } else {
    const a = new Date(t0);
    for (let d = new Date(a.getFullYear(), a.getMonth() + (a.getDate() > 1 ? 1 : 0), 1); d.getTime() < t1; d.setMonth(d.getMonth() + 1))
      xTicks.push({ t: d.getTime(), label: MONTHS[d.getMonth()]! });
  }
  const tStep = Math.ceil(44 / Math.max(pw / Math.max(xTicks.length, 1), 1));

  // даты для перекрестия: все покупки в видимом окне
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.t)).filter(visible))].sort((a, b) => a - b);
  const hoverT = hover !== null ? dates[hover] : undefined;

  const near = (s: Series, t: number) => {
    let best: (PricePoint & { t: number }) | undefined;
    for (const p of s.points) if (Math.abs(p.t - t) <= 10 * DAY && (!best || Math.abs(p.t - t) < Math.abs(best.t - t))) best = p;
    return best;
  };

  const svgX = (e: PointerEvent<HTMLDivElement>) => e.clientX - (e.currentTarget.querySelector("svg")?.getBoundingClientRect().left ?? 0);
  const tAt = (x: number) => t0 + ((Math.min(Math.max(x, L), L + pw) - L) / pw) * (t1 - t0);
  // мышь и перо: протянуть по графику — приблизить период; касание — только подсказка
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== "touch") setDrag({ x0: svgX(e), x1: svgX(e) });
    onMove(e);
  };
  const onUp = () => {
    if (drag && Math.abs(drag.x1 - drag.x0) > 12) {
      const a = tAt(Math.min(drag.x0, drag.x1));
      const b = tAt(Math.max(drag.x0, drag.x1));
      if (b - a > 3 * DAY) {
        setZoom([a, b]);
        setHover(null);
      }
    }
    setDrag(null);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (drag) setDrag({ ...drag, x1: svgX(e) });
    if (!dates.length) return;
    const x = svgX(e);
    let bi = 0;
    dates.forEach((t, i) => {
      if (Math.abs(X(t) - x) < Math.abs(X(dates[bi]!) - x)) bi = i;
    });
    setHover(bi);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!dates.length) return;
    if (e.key === "ArrowRight") setHover((h) => Math.min((h ?? -1) + 1, dates.length - 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max((h ?? dates.length) - 1, 0));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  // подписи у концов линий: без наложений, иначе — только легенда
  const ends = series
    .filter((s) => s.line)
    .map((s) => {
      const last = [...s.points].reverse().find((p) => visible(p.t));
      return last ? { s, y: Y(Number(last.price)), x: X(last.t) } : null;
    })
    .filter((v): v is { s: Series; y: number; x: number } => !!v)
    .sort((a, b) => a.y - b.y);
  const endsFit = endLabels && ends.every((e, i) => i === 0 || e.y - ends[i - 1]!.y >= 15);

  const u = unitText(history.unit);
  const qPrices = qPoints.map((p) => Number(p.price));
  const summary =
    `Цены «${history.label}», сом за ${u}. ` +
    series.filter((s) => s.line).map((s) => `${s.name}: от ${int(Math.min(...s.points.map((p) => Number(p.price))))} до ${int(Math.max(...s.points.map((p) => Number(p.price))))}`).join("; ") +
    (market.steps.length ? `; рынок около ${int(median(market.steps.map((s) => s.med)))}` : "") +
    (reference ? `; эталон ${int(reference)}` : "") +
    (qPrices.length ? `. Строк вопроса: ${qPrices.length}.` : ".");

  const path = (pts: { t: number; price: string }[]) =>
    pts.filter((p) => visible(p.t)).map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(Number(p.price)).toFixed(1)}`).join("");
  const band = market.steps.filter((s) => s.t >= t0 - 31 * DAY && s.t < t1 + 31 * DAY);
  const bandPath = band.length
    ? `M${band.map((s) => `${X(s.t).toFixed(1)},${Y(s.med * (1 + tolerance)).toFixed(1)}`).join("L")}` +
      `L${[...band].reverse().map((s) => `${X(s.t).toFixed(1)},${Y(s.med * (1 - tolerance)).toFixed(1)}`).join("L")}Z`
    : "";
  const medPath = band.length ? `M${band.map((s) => `${X(s.t).toFixed(1)},${Y(s.med).toFixed(1)}`).join("L")}` : "";

  const tipLeft = hoverT !== undefined ? X(hoverT) : 0;
  const tipRight = tipLeft > width * 0.55;
  const refAt = (t: number) => reference ?? market.byMonth.get(monthKey(t)) ?? null;

  // подсказка: на десктопе — у курсора, на узком экране — панелью под графиком
  const dock = width < 560;
  const tip = hoverT !== undefined ? (
    <>
        <span className="ch-tip-date">{date(new Date(hoverT).toISOString().slice(0, 10))}</span>
        {[...series].reverse().map((s) => {
          const p = near(s, hoverT);
          if (!p) return null;
          const over = p.line_id && lineIds.has(p.line_id) && Number(p.overpayment) > 0 ? Number(p.overpayment) : 0;
          return (
            <span key={s.name} className="ch-tip-row">
              <i style={{ background: s.color }} />
              <strong>{int(p.price)}</strong>
              <span>
                {s.name}
                {p.qty ? ` · ${qty(p.qty)} ${u}` : ""}
                {over ? ` · переплата ${int(over)} сом` : ""}
              </span>
            </span>
          );
        })}
        {market.byMonth.get(monthKey(hoverT)) !== undefined && (
          <span className="ch-tip-row">
            <i className="ch-tip-market" />
            <strong>{int(market.byMonth.get(monthKey(hoverT))!)}</strong>
            <span>рынок, медиана месяца</span>
          </span>
        )}
        {(() => {
          const s = series.find((x) => x.question);
          const p = s && near(s, hoverT);
          return p?.ref ? <span className="mono ch-tip-doc">Поступление {docNumber(p.ref)}</span> : null;
        })()}
    </>
  ) : null;

  const pickRange = (r: Range) => {
    setRange(r);
    setZoom(null);
    setHover(null);
  };

  return (
    <figure className={`chart${full ? " chart-full" : ""}`} role={full ? "dialog" : undefined} aria-label={full ? title : undefined}>
      <div className="chart-bar">
        {full && title ? (
          <span className="chart-title">{title}</span>
        ) : (
          <span className="chart-hint desktop-only">Протяните мышью по графику, чтобы приблизить период</span>
        )}
        <div className="chart-tools">
          {hasQuestionRange && (
            <div className="seg" role="group" aria-label="Период графика">
              <button type="button" className={range === "all" && !zoom ? "on" : ""} aria-pressed={range === "all" && !zoom} onClick={() => pickRange("all")}>
                Весь период
              </button>
              <button type="button" className={range === "question" && !zoom ? "on" : ""} aria-pressed={range === "question" && !zoom} onClick={() => pickRange("question")}>
                Период вопроса
              </button>
            </div>
          )}
          {zoom && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => pickRange(range)}>
              <Icon name="zoomOut" size={16} />
              Сбросить масштаб
            </button>
          )}
          <button
            type="button"
            className="btn btn-icon"
            onClick={toggle}
            aria-label={full ? "Свернуть график" : "Развернуть график на весь экран"}
            title={full ? "Свернуть (Esc)" : "На весь экран"}
          >
            <Icon name={full ? "minimize" : "maximize"} size={16} strokeWidth={2} />
          </button>
        </div>
      </div>
      {full && <p className="chart-rotate">Поверните телефон горизонтально — график станет шире.</p>}
      <div
        className="chart-plot"
        ref={box}
        tabIndex={0}
        role="group"
        aria-label={`${summary} Стрелки влево и вправо — по датам покупок.`}
        onPointerMove={onMove}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerLeave={(e) => {
          if (e.pointerType === "touch") return; // на касании «уход» приходит сразу после отпускания пальца
          setHover(null);
          setDrag(null);
        }}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
      >
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary}>
          <defs>
            <clipPath id="plot-clip">
              <rect x={L} y={T - 8} width={pw} height={ph + 16} />
            </clipPath>
          </defs>
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={L} x2={L + pw} y1={Y(v)} y2={Y(v)} className="ch-grid" />
              <text x={L - 8} y={Y(v) + 4} textAnchor="end" className="ch-tick">
                {tick(v, big)}
              </text>
            </g>
          ))}
          {xTicks.map((m, i) =>
            i % tStep ? null : (
              <text key={m.t} x={X(m.t) + 2} y={height - 7} className="ch-tick">
                {m.label}
              </text>
            ),
          )}
          <g clipPath="url(#plot-clip)">
            {bandPath && <path d={bandPath} className="ch-band" />}
            {medPath && <path d={medPath} className="ch-median" />}
            {reference !== null && <line x1={L} x2={L + pw} y1={Y(reference)} y2={Y(reference)} className="ch-ref" />}
            {/* переплата на единицу: от эталона (или медианы рынка месяца) до цены строки вопроса */}
            {qPoints.filter((p) => visible(p.t)).map((p) => {
              const ref = refAt(p.t);
              return ref !== null && Number(p.price) > ref ? (
                <line key={`gap${p.line_id}`} x1={X(p.t)} x2={X(p.t)} y1={Y(ref)} y2={Y(Number(p.price))} className="ch-gap" />
              ) : null;
            })}
            {market.pts.filter((p) => visible(p.t)).map((p, i) => (
              <rect key={`m${i}`} x={X(p.t) - 3} y={Y(Number(p.price)) - 3} width={6} height={6} className="ch-market" />
            ))}
            {series.map((s) =>
              s.line ? <path key={`l${s.name}`} d={path(s.points)} fill="none" stroke={s.color} className={s.question ? "ch-line ch-line-q" : "ch-line"} /> : null,
            )}
            {series.map((s) =>
              s.points.filter((p) => visible(p.t)).map((p, i) => (
                <g key={`${s.name}${i}`}>
                  <circle cx={X(p.t)} cy={Y(Number(p.price))} r={4} fill={s.color} className="ch-dot" />
                  {p.line_id && lineIds.has(p.line_id) && <circle cx={X(p.t)} cy={Y(Number(p.price))} r={7.5} className="ch-ring" />}
                </g>
              )),
            )}
          </g>
          {reference !== null && (
            <text x={L + pw - 4} y={Y(reference) - 6} textAnchor="end" className="ch-ref-text">
              эталон {int(reference)}
            </text>
          )}
          {endsFit &&
            ends.map((e) => (
              <g key={`end${e.s.name}`}>
                <line x1={L + pw + 8} x2={L + pw + 16} y1={e.y} y2={e.y} stroke={e.s.color} strokeWidth={2} strokeLinecap="round" />
                <text x={L + pw + 20} y={e.y + 4} className="ch-end">
                  {short(e.s.name).slice(0, 14)}
                </text>
              </g>
            ))}
          {hoverT !== undefined && !drag && <line x1={X(hoverT)} x2={X(hoverT)} y1={T} y2={T + ph} className="ch-cross" />}
          {drag && Math.abs(drag.x1 - drag.x0) > 2 && (
            <rect
              x={Math.max(L, Math.min(drag.x0, drag.x1))}
              y={T}
              width={Math.min(Math.abs(drag.x1 - drag.x0), L + pw - Math.max(L, Math.min(drag.x0, drag.x1)))}
              height={ph}
              className="ch-brush"
            />
          )}
        </svg>
        {tip && !dock && (
          <div className="ch-tip" style={tipRight ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }}>
            {tip}
          </div>
        )}
      </div>
      {tip && dock && (
        <div className="ch-tip ch-tip-dock" role="status">
          {tip}
        </div>
      )}
      <figcaption className="legend">
        <span className="lg-caption">сом за {u}</span>
        {series
          .filter((s) => s.line)
          .reverse()
          .map((s) => (
            <span key={s.name}>
              <i className="lg-line" style={{ background: s.color }} /> {s.name}
            </span>
          ))}
        {qPoints.length > 0 && (
          <span>
            <i className="lg-ring" /> строки вопроса
          </span>
        )}
        {series.some((s) => !s.line) && (
          <span>
            <i className="lg-dot" style={{ background: OTHER }} /> Другие поставщики
          </span>
        )}
        {market.steps.length > 0 && (
          <span>
            <i className="lg-band" /> рынок, медиана ± {Math.round(tolerance * 100)}%
          </span>
        )}
        {reference !== null && (
          <span>
            <i className="lg-ref" /> эталон
          </span>
        )}
      </figcaption>
    </figure>
  );
}
