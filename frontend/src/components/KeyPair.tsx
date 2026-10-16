// Два ключа: сегмент 1 — снабжение ответило, сегмент 2 — собственник решил.
// Пусто — контур, ответ — заливка акцентом, решение — заливка по смыслу (штриховка — ошибка учёта).
import type { FindingStatus } from "../api/client";
import { statusText } from "../questions";

type Size = "s" | "m" | "l";

export function KeyPair({ status, size = "m" }: { status: FindingStatus; size?: Size }) {
  const k1 = status !== "open" ? "on" : "off";
  const k2 = status === "confirmed" || status === "justified" || status === "data_error" ? status : "off";
  return (
    <span className={`keypair keypair-${size}`} aria-hidden="true">
      <span className={`key key1-${k1}`} />
      <span className={`key key2-${k2}`} />
    </span>
  );
}

/** Ключи + статус словами: цвет никогда не единственный носитель смысла. */
export function KeyStatus({ status, size = "s", code }: { status: FindingStatus; size?: Size; code?: string }) {
  return (
    <span className={`keystatus st-${status}`}>
      <KeyPair status={status} size={size} />
      <span>{statusText(status, code)}</span>
    </span>
  );
}

/** Один сегмент — для подписей «Ключ 1 · …», «12 ждут вашего ключа». */
export function KeySeg({ state, size = "m" }: { state: "on" | "off" | FindingStatus; size?: Size }) {
  const cls = state === "on" || state === "off" ? `key1-${state}` : `key2-${state}`;
  return (
    <span className={`keypair keypair-${size}`} aria-hidden="true">
      <span className={`key ${cls}`} />
    </span>
  );
}
