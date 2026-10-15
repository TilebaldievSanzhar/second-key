// Разрез сводки: строка с полосой доли от максимума. Нажатие ведёт во входящие с фильтром.
import { Link } from "react-router";
import type { Breakdown } from "../api/client";
import { LINES, QUESTIONS, count, int } from "../format";
import { RULE_TEXT } from "../questions";

export type Cut = "supplier" | "responsible" | "material" | "rule";

export const CUT_TITLE: Record<Cut, string> = {
  supplier: "Поставщики",
  responsible: "Ответственные",
  material: "Материалы",
  rule: "Правила",
};

// фильтр входящих по разрезу: поставщик, ответственный, правило; у материалов фильтра нет
const filterHref = (cut: Cut, key: string) =>
  cut === "supplier" ? `/findings?supplier=${encodeURIComponent(key)}` : cut === "responsible" ? `/findings?responsible=${encodeURIComponent(key)}` : cut === "rule" ? `/findings?code=${key}` : null;

export function BreakdownList({ cut, rows, limit = 5 }: { cut: Cut; rows: Breakdown[]; limit?: number }) {
  const shown = rows.filter((r) => Number(r.overpayment) > 0).slice(0, limit);
  if (!shown.length) return <p className="cut-empty">Переплат в этом разрезе нет</p>;
  const max = Math.max(...shown.map((r) => Number(r.overpayment)));
  return (
    <ul className="cut">
      {shown.map((r) => {
        const label = cut === "rule" ? (RULE_TEXT[r.key] ?? r.label) : cut === "material" ? capital(r.label) : r.label;
        const note = cut === "rule" ? count(r.findings ?? 0, QUESTIONS) : count(r.lines, LINES);
        const href = filterHref(cut, r.key);
        const body = (
          <>
            <span className="cut-label">{label}</span>
            <span className="cut-note">{note}</span>
            <span className="cut-amt">{int(r.overpayment)}&nbsp;сом</span>
            <span className="cut-bar" aria-hidden="true">
              <span style={{ width: `${(Number(r.overpayment) / max) * 100}%` }} />
            </span>
          </>
        );
        return <li key={r.key}>{href ? <Link to={href} className="cut-row">{body}</Link> : <span className="cut-row">{body}</span>}</li>;
      })}
    </ul>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
