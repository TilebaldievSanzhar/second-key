// Матрица «поставщики × снабженцы»: кто у кого покупает и сколько.
// Оборот пары — один оттенок синего от светлого к тёмному (до 5 ступеней по квантилям; ступени проверены
// validate_palette.js --ordinal в обеих темах). Связка — рамка «внимание» снаружи клетки и значок; одиночный
// след пары без вопроса — точка. Значения — текстом в клетке (таблица и есть табличный вид), подробности — подсказкой
// при наведении и фокусе. Нажатие: связка — к вопросу, иначе — к карточке поставщика.
import { useMemo, useRef, useState, type FocusEvent, type PointerEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { LinksOverview, MatrixCell } from "../api/client";
import { LINES, compact, count, money, share } from "../format";
import { TRACE_TEXT } from "../questions";
import { Icon } from "./Icon";

const SEP = "\u0000";
const key = (supplier: string, buyer: string | null | undefined) => `${supplier}${SEP}${buyer ?? ""}`;
const buyerName = (b: string | null | undefined) => b ?? "Без ответственного";

/** Пороги ступеней: четыре точки, равномерные на логарифмической шкале между меньшим и большим оборотом,
 *  округлённые до ряда 1–2–5 (1 млн, 2 млн, 5 млн, 10 млн…) — легенда читается без калькулятора. */
function thresholds(values: number[]): number[] {
  const v = values.filter((x) => x > 0);
  if (v.length < 2) return [];
  const lo = Math.log10(Math.min(...v));
  const hi = Math.log10(Math.max(...v));
  if (hi - lo < 0.3) return [];
  const nice = (x: number) => {
    const e = Math.floor(Math.log10(x));
    const cands = [1, 2, 5, 10].map((m) => m * 10 ** e);
    return cands.reduce((a, b) => (Math.abs(Math.log10(b) - Math.log10(x)) < Math.abs(Math.log10(a) - Math.log10(x)) ? b : a));
  };
  const out: number[] = [];
  for (const k of [1, 2, 3, 4]) {
    const t = nice(10 ** (lo + ((hi - lo) * k) / 5));
    if (t > (out[out.length - 1] ?? 0)) out.push(t);
  }
  return out;
}

type Tip = { cell: MatrixCell; x: number; y: number; below: boolean };

export function LinkMatrix({ data, runId, limit = 12 }: { data: LinksOverview; runId: string; limit?: number }) {
  const navigate = useNavigate();
  const [all, setAll] = useState(false);
  const [tip, setTip] = useState<Tip | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  const cells = useMemo(() => new Map(data.cells.map((c) => [key(c.supplier, c.buyer), c])), [data.cells]);
  const linkOf = useMemo(() => new Map(data.links.map((l) => [key(l.supplier ?? "", l.responsible), l.finding_id])), [data.links]);
  const ts = useMemo(() => thresholds(data.cells.map((c) => Number(c.spend))), [data.cells]);
  const steps = ts.length + 1;
  const heat = (v: number) => {
    const b = ts.filter((t) => v >= t).length; // 0 … steps-1
    return steps > 1 ? 1 + Math.round((b * 4) / (steps - 1)) : 3;
  };

  const important = data.suppliers.filter((s) => s.linked || s.traces.length);
  const top = data.suppliers.slice(0, limit);
  const rows = all ? data.suppliers : [...top, ...important.filter((s) => !top.includes(s))];
  const hidden = data.suppliers.length - rows.length;

  const show = (cell: MatrixCell) => (e: PointerEvent<HTMLButtonElement> | FocusEvent<HTMLButtonElement>) => {
    if ("pointerType" in e && e.pointerType === "touch") return; // на касании — фокус, он тоже покажет подсказку
    const r = e.currentTarget.getBoundingClientRect();
    const w = wrap.current?.getBoundingClientRect();
    if (!w) return;
    const x = Math.min(Math.max(r.left - w.left + r.width / 2, 124), w.width - 124);
    const top = r.top - w.top;
    setTip({ cell, x, y: top < 150 ? top + r.height + 8 : top - 8, below: top < 150 });
  };
  const hide = () => setTip(null);

  const legend = ts.length ? [0, ...ts].map((from, i) => ({ from, to: ts[i] ?? null, cls: heat(from) })) : [];

  return (
    <div className="matrix-wrap" ref={wrap}>
      <div className="matrix-legend" aria-hidden="true">
        <span className="ml-title">Оборот пары, сом</span>
        {legend.map((l) => (
          <span key={l.from} className="ml-item">
            <i className={`ml-swatch heat-${l.cls}`} />
            {l.to === null ? `от ${compact(l.from)}` : l.from === 0 ? `до ${compact(l.to)}` : `${compact(l.from)} – ${compact(l.to)}`}
          </span>
        ))}
        <span className="ml-item">
          <i className="ml-swatch ml-flag" />
          связка — вопрос задан
        </span>
        <span className="ml-item">
          <i className="mdot" />
          след без вопроса
        </span>
      </div>
      {data.buyers.length > 2 && <p className="muted small mobile-only">Таблица шире экрана — листайте вбок.</p>}
      <div className="matrix-scroll">
        <table className="matrix">
          <caption className="sr-only">Оборот по парам «поставщик — снабженец» за период проверки</caption>
          <thead>
            <tr>
              <th scope="col" className="mcorner">
                Поставщик
              </th>
              {data.buyers.map((b) => (
                <th key={b.name ?? SEP} scope="col" className="mhead-cell">
                  <span className="mh-name">{buyerName(b.name)}</span>
                  <span className="mh-sub">{compact(b.spend)}</span>
                </th>
              ))}
              <th scope="col" className="mhead-cell mtotal">
                Всего
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <th scope="row" className="mrow">
                  <Link to={`/suppliers/${s.id}?run=${runId}`} className="mrow-name" title={s.name}>
                    {s.name}
                  </Link>
                  {s.linked && <span className="mrow-flag">связка</span>}
                </th>
                {data.buyers.map((b) => {
                  const c = cells.get(key(s.name, b.name));
                  if (!c) return <td key={b.name ?? SEP} className="mcell mcell-empty" aria-label="нет покупок" />;
                  const linkId = linkOf.get(key(s.name, b.name)) ?? (c.linked ? linkOf.get(key(s.name, null)) : undefined);
                  const label = `${s.name} — ${buyerName(b.name)}: ${money(c.spend)}, ${count(c.lines, LINES)}, ${share(c.share)} оборота поставщика${c.linked ? ". Связка — вопрос задан" : ""}${c.codes.length && !c.linked ? `. След без вопроса: ${c.codes.map((x) => TRACE_TEXT[x] ?? x).join(", ")}` : ""}`;
                  return (
                    <td key={b.name ?? SEP} className={`mcell heat-${heat(Number(c.spend))}${c.linked ? " mcell-flag" : ""}`}>
                      <button
                        type="button"
                        aria-label={label}
                        onPointerEnter={show(c)}
                        onPointerLeave={hide}
                        onFocus={show(c)}
                        onBlur={hide}
                        onClick={() => navigate(linkId ? `/findings/${linkId}` : `/suppliers/${s.id}?run=${runId}`)}
                      >
                        {c.linked && <Icon name="link" size={12} strokeWidth={2.5} />}
                        {compact(c.spend)}
                        {!c.linked && c.codes.length > 0 && <i className="mdot" />}
                      </button>
                    </td>
                  );
                })}
                <td className="mcell mtotal num">{compact(s.spend)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <button type="button" className="btn btn-link matrix-more" onClick={() => setAll(true)}>
          Показать всех поставщиков · ещё {hidden}
        </button>
      )}
      {tip && (
        <div className={`ch-tip matrix-tip${tip.below ? " below" : ""}`} style={{ left: tip.x, top: tip.y }} role="presentation">
          <span className="ch-tip-date">
            {tip.cell.supplier} · {buyerName(tip.cell.buyer)}
          </span>
          <span>
            <strong>{money(tip.cell.spend)}</strong> · {count(tip.cell.lines, LINES)}
          </span>
          <span>{share(tip.cell.share)} оборота поставщика</span>
          {tip.cell.linked && <span className="matrix-tip-flag">Связка — вопрос задан</span>}
          {!tip.cell.linked && tip.cell.codes.length > 0 && <span>След без вопроса: {tip.cell.codes.map((x) => TRACE_TEXT[x] ?? x).join(", ")}</span>}
        </div>
      )}
    </div>
  );
}
