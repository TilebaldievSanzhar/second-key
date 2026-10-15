// Выбор проверки: нативный select поверх подписи — на телефоне открывается системный список.
import type { Run } from "../api/client";
import { date, period } from "../format";
import { useCurrentRun } from "../session";
import { Icon } from "./Icon";

const label = (r: Run) => `${period(r.period_start, r.period_end)} · проверка от ${date(r.created_at)}`;

export function RunPicker({ variant = "plain", text }: { variant?: "plain" | "boxed"; text?: (r: Run) => string }) {
  const { run, runs, select } = useCurrentRun();
  const done = runs.filter((r) => r.status === "done");
  if (!run) return null;
  const shown = text ? text(run) : period(run.period_start, run.period_end);
  return (
    <label className={`picker picker-${variant}`}>
      <span className="picker-text">{shown}</span>
      {done.length > 1 && <Icon name="down" size={16} strokeWidth={2} />}
      {done.length > 1 && (
        <select aria-label="Проверка" value={run.id} onChange={(e) => select(e.target.value)}>
          {done.map((r) => (
            <option key={r.id} value={r.id}>
              {label(r)}
            </option>
          ))}
        </select>
      )}
    </label>
  );
}

export function BrandMark({ size = "m" }: { size?: "m" | "l" }) {
  return (
    <span className={`brand-mark brand-mark-${size}`} aria-hidden="true">
      <span />
      <span />
    </span>
  );
}
