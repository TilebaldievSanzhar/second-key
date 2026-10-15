// Сумма в сомах. Возможная — янтарь, подтверждённая — красный с галочкой, остальное — чернила.
import { int, money } from "../format";
import { Icon } from "./Icon";

type Tone = "over" | "confirmed" | "neutral" | "muted";
type Size = "hero" | "xl" | "l" | "m" | "s";

export function Money({ value, tone = "neutral", size = "m", unit = "сом" }: {
  value: string | number | null | undefined;
  tone?: Tone;
  size?: Size;
  unit?: string;
}) {
  if (value === null || value === undefined) return <span className="money money-muted">—</span>;
  return (
    <span className={`money money-${tone} money-${size}${long(value) ? " money-long" : ""}`} aria-label={money(value)}>
      {tone === "confirmed" && <Icon name="check" size={size === "hero" || size === "xl" ? 18 : 14} strokeWidth={3} />}
      <span aria-hidden="true">{int(value)}</span>
      {unit && <span className="money-unit" aria-hidden="true">{"\u00a0" + unit}</span>}
    </span>
  );
}

// суммы от миллиарда: на 375 px крупный кегль не помещается — уменьшаем, а не обрезаем
const long = (v: string | number) => Math.abs(Number(v)) >= 1e9;
