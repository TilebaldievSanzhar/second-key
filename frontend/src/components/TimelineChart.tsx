// Возможная переплата по месяцам, столбцы разбиты по правилам (строка — правилу с наибольшим вкладом).
// Палитра правил проверена validate_palette.js (соседние пары в стопке, поверхность #FDFDFB).
// Подпись — только у максимального месяца; остальное — ось, подсказка и скрытая таблица для скринридеров.
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Schemas } from "../api/client";
import { MONTHS, int, moneyShort, pct } from "../format";
import { RULE_TEXT } from "../questions";

type Month = Schemas["TimelineMonth"];

// порядок в стопке снизу вверх и цвета — фиксированы за правилом
const RULES: { code: string; color: string }[] = [
  { code: "P-01", color: "#256abf" },
  { code: "P-03", color: "#199e70" },
  { code: "P-02", color: "#6250d6" },
  { code: "P-06", color: "#9A5B00" },
];

const label = (m: string) => {
  const [y, mm] = m.split("-").map(Number) as [number, number];
  return `${MONTHS[mm - 1]} ${String(y).slice(2)}`;
};
const compact = (v: number) =>
  v >= 1e6 ? `${(v / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн` : v >= 1e3 ? `${Math.round(v / 1e3)} тыс` : String(v);

export function TimelineChart({ months, height = 220 }: { months: Month[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [active, setActive] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const used = RULES.filter((r) => months.some((m) => Number(m.by_rule[r.code] ?? 0) > 0));
  const other = months.map((m) =>
    Object.entries(m.by_rule).filter(([c]) => !RULES.some((r) => r.code === c)).reduce((a, [, v]) => a + Number(v), 0),
  );
  const totals = months.map((m) => Number(m.overpayment));
  const max = Math.max(...totals, 1);
  const raw = max / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  const stepV = [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) ?? 10 * p;
  const top = Math.ceil(max / stepV) * stepV;
  const ticks = Array.from({ length: Math.round(top / stepV) + 1 }, (_, i) => i * stepV);

  const L = 56, R = 8, T = 22, B = 26;
  const pw = width - L - R;
  const ph = height - T - B;
  const slot = pw / Math.max(months.length, 1);
  const bw = Math.min(24, slot * 0.6);
  const Y = (v: number) => T + ph - (v / top) * ph;
  const peak = totals.indexOf(Math.max(...totals));
  const step = slot < 34 ? 2 : 1;

  // столбец под указателем — по координате: мышь, перо и палец ведут себя одинаково
  const pick = (e: PointerEvent<HTMLDivElement>) => {
    const x = e.clientX - e.currentTarget.getBoundingClientRect().left - 8; // 8 — внутренний отступ .chart-plot
    const i = Math.floor((x - L) / slot);
    if (i >= 0 && i < months.length) setActive(i);
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowRight") setActive((a) => Math.min((a ?? -1) + 1, months.length - 1));
    else if (e.key === "ArrowLeft") setActive((a) => Math.max((a ?? months.length) - 1, 0));
    else if (e.key === "Escape") setActive(null);
    else return;
    e.preventDefault();
  };

  const m = active !== null ? months[active] : undefined;
  // подсказка: на десктопе — у курсора, на узком экране — панелью под графиком
  const dock = width < 560;
  const tip = m ? (
    <>
        <span className="ch-tip-date">{label(m.month)}</span>
        <span className="ch-tip-row">
          <strong>{moneyShort(m.overpayment)}</strong>
          <span>возможная переплата · {pct(m.overpayment, m.spend)} закупок месяца</span>
        </span>
        {used.map((r) =>
          Number(m.by_rule[r.code] ?? 0) > 0 ? (
            <span key={r.code} className="ch-tip-row">
              <i style={{ background: r.color }} />
              <strong>{int(m.by_rule[r.code])}</strong>
              <span>{RULE_TEXT[r.code]}</span>
            </span>
          ) : null,
        )}
        <span className="ch-tip-doc">
          {m.flagged_lines} из {m.lines} строк с переплатой · закупки {moneyShort(m.spend)}
        </span>
    </>
  ) : null;

  return (
    <figure className="chart">
      <figcaption className="legend legend-top">
        {used.map((r) => (
          <span key={r.code}>
            <i className="lg-rect" style={{ background: r.color }} /> {RULE_TEXT[r.code]}
          </span>
        ))}
        {other.some((v) => v > 0) && (
          <span>
            <i className="lg-rect" style={{ background: "#8a8f8b" }} /> Другие правила
          </span>
        )}
      </figcaption>
      <div
        className="chart-plot"
        ref={box}
        tabIndex={0}
        role="group"
        aria-label="Возможная переплата по месяцам. Стрелки влево и вправо — по месяцам."
        onKeyDown={onKey}
        onPointerMove={pick}
        onPointerDown={pick}
        onPointerLeave={(e) => e.pointerType !== "touch" && setActive(null)} // на касании «уход» приходит сразу после отпускания пальца
        onBlur={() => setActive(null)}
      >
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          {ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={L + pw} y1={Y(v)} y2={Y(v)} className="ch-grid" />
              <text x={L - 8} y={Y(v) + 4} textAnchor="end" className="ch-tick">
                {compact(v)}
              </text>
            </g>
          ))}
          {months.map((mo, i) => {
            const cx = L + slot * i + slot / 2;
            let acc = 0;
            const segs = [...used.map((r) => ({ color: r.color, v: Number(mo.by_rule[r.code] ?? 0) })), { color: "#8a8f8b", v: other[i]! }].filter(
              (s) => s.v > 0,
            );
            return (
              <g key={mo.month} className={active === i ? "tl-col tl-col-on" : "tl-col"}>
                {segs.map((s, j) => {
                  const y0 = Y(acc); // низ сегмента
                  acc += s.v;
                  const last = j === segs.length - 1;
                  const yt = Y(acc) + (last ? 0 : 2); // 2 px зазор над каждым сегментом, кроме верхнего
                  const h = Math.max(y0 - yt, 1);
                  const r = last ? Math.min(4, h) : 0; // скругление 4 px — только у края данных
                  const xl = cx - bw / 2;
                  const xr = cx + bw / 2;
                  return (
                    <path
                      key={j}
                      fill={s.color}
                      d={`M${xl},${yt + h}V${yt + r}${r ? `Q${xl},${yt} ${xl + r},${yt}` : ""}H${xr - r}${r ? `Q${xr},${yt} ${xr},${yt + r}` : ""}V${yt + h}Z`}
                    />
                  );
                })}
                {i === peak && totals[i]! > 0 && (
                  <text x={cx} y={Y(totals[i]!) - 6} textAnchor="middle" className="ch-cap">
                    {compact(totals[i]!)}
                  </text>
                )}
                {i % step === 0 && (
                  <text x={cx} y={height - 7} textAnchor="middle" className="ch-tick">
                    {label(mo.month)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {tip && !dock && (
          <div className="ch-tip" style={(active ?? 0) > months.length / 2 ? { right: width - (L + slot * active! + slot / 2) + 14 } : { left: L + slot * active! + slot / 2 + 14 }}>
            {tip}
          </div>
        )}
      </div>
      {tip && dock && (
        <div className="ch-tip ch-tip-dock" role="status">
          {tip}
        </div>
      )}
      {/* таблица шире 1 px не сжимается — прячем блок-обёртку, а не саму таблицу */}
      <div className="sr-only">
      <table>
        <caption>Возможная переплата по месяцам, сом</caption>
        <thead>
          <tr>
            <th>Месяц</th>
            <th>Закупки</th>
            <th>Переплата</th>
            {used.map((r) => (
              <th key={r.code}>{RULE_TEXT[r.code]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {months.map((mo) => (
            <tr key={mo.month}>
              <td>{label(mo.month)}</td>
              <td>{int(mo.spend)}</td>
              <td>{int(mo.overpayment)}</td>
              {used.map((r) => (
                <td key={r.code}>{int(mo.by_rule[r.code] ?? 0)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </figure>
  );
}
