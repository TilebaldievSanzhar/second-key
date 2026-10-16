// Строка вопроса: вопрос и мета слева, ключи и статус, сумма справа. Сигналы — тише денежных.
import { Link } from "react-router";
import type { Finding } from "../api/client";
import { int } from "../format";
import { RULE_TEXT, isSignal, meta, questionTitle } from "../questions";
import { KeyStatus } from "./KeyPair";

function Amount({ f }: { f: Finding }) {
  if (f.code === "LINK") return <span className="row-amt row-amt-signal">связка</span>;
  if (isSignal(f)) return <span className="row-amt row-amt-signal">сигнал</span>;
  const tone = f.status === "confirmed" ? "confirmed" : f.status === "open" || f.status === "answered" ? "over" : "muted";
  return <span className={`row-amt row-amt-${tone}`}>{int(f.overpayment)}&nbsp;сом</span>;
}

export function FindingRow({
  f,
  selected,
  showRule = false,
  className,
}: {
  f: Finding;
  selected?: boolean;
  showRule?: boolean;
  className?: string;
}) {
  const cls = ["frow", selected && "frow-selected", isSignal(f) && "frow-signal", showRule && "frow-wide", className];
  return (
    <li className={cls.filter(Boolean).join(" ")}>
      <Link to={`/findings/${f.id}`} className="frow-link" aria-current={selected ? "true" : undefined}>
        <span className="frow-main">
          <span className="frow-q">{questionTitle(f)}</span>
          <span className="frow-meta">{meta(f)}</span>
        </span>
        {showRule && <span className="frow-rule">{RULE_TEXT[f.code] ?? f.title}</span>}
        <span className="frow-key">
          <KeyStatus status={f.status} code={f.code} />
        </span>
        <Amount f={f} />
      </Link>
    </li>
  );
}
