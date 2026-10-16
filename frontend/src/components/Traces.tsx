// Следы связки: три группы (цена, поведение, поставщик) и сами следы — что увидели, с цифрами.
// Группа показана словом и заливкой — не только цветом.
import type { Trace } from "../api/client";
import { GROUPS, TRACE_TEXT, type TraceGroup } from "../questions";

export function TraceGroups({ groups, size = "m" }: { groups: string[]; size?: "s" | "m" }) {
  const on = new Set(groups);
  const label = `Сошлись следы: ${GROUPS.filter((g) => on.has(g.key)).map((g) => g.label.toLowerCase()).join(", ") || "нет"}`;
  return (
    <span className={`trace-groups trace-groups-${size}`} role="img" aria-label={label} title={label}>
      {GROUPS.map((g) => (
        <span key={g.key} className={`trace-pill${on.has(g.key) ? " on" : ""}`} aria-hidden="true">
          {g.label}
        </span>
      ))}
    </span>
  );
}

/** Следы по группам; compact — без подписей групп (в узкой карточке связки). */
export function TraceList({ traces, compact = false }: { traces: Trace[]; compact?: boolean }) {
  const byGroup = GROUPS.map((g) => ({ ...g, items: traces.filter((t) => t.group === g.key) })).filter((g) => g.items.length);
  if (!byGroup.length) return null;
  return (
    <div className={`trace-list${compact ? " trace-list-compact" : ""}`}>
      {byGroup.map((g) => (
        <section key={g.key} className={`trace-group trace-${g.key as TraceGroup}`}>
          <h3 className="trace-group-name">
            {g.label}
            {!compact && <span className="trace-group-hint">{g.hint}</span>}
          </h3>
          <ul>
            {g.items.map((t, i) => (
              <li key={t.code + i} className="trace-item">
                <span className="trace-title">{TRACE_TEXT[t.code] ?? t.code}</span>
                <span className="trace-text">{t.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
