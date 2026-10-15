// Язык вопросов: правила словами, заголовок-вопрос из полей находки, статусы двух ключей.
// Интерфейс задаёт вопрос, а не обвиняет (docs/design/FRONTEND_SPEC.md § 8).
import type { Finding, FindingStatus } from "./api/client";
import { share } from "./format";

export const RULE_TEXT: Record<string, string> = {
  "P-01": "Дороже, чем у другого поставщика в те же недели",
  "P-02": "Цена поставщика растёт быстрее рынка",
  "P-03": "Дороже рынка",
  "P-04": "Почти всё у одного, и он дороже",
  "P-05": "Дробление закупки ниже порога согласования",
  "P-06": "Документ учтён дважды",
  "P-07": "Цена не сходится с единицей измерения",
};

export const STATUS_TEXT: Record<FindingStatus, string> = {
  open: "Ждёт ответа",
  answered: "Есть ответ",
  confirmed: "Переплата подтверждена",
  justified: "Обосновано",
  data_error: "Ошибка учёта",
};

export const WAITING: FindingStatus[] = ["open", "answered"];
export const DECIDED: FindingStatus[] = ["confirmed", "justified", "data_error"];
export const isDecided = (s: FindingStatus) => DECIDED.includes(s);

export const DECISIONS = [
  { status: "confirmed", label: "Переплата подтверждена", key: "1" },
  { status: "justified", label: "Обосновано", key: "2" },
  { status: "data_error", label: "Ошибка учёта", key: "3" },
] as const;
export type Decision = (typeof DECISIONS)[number]["status"];

export const REASONS = [
  "Срочная поставка",
  "Доставка включена в цену",
  "Другая марка или качество",
  "Отсрочка платежа",
  "Ошибка в учёте",
  "Другое",
] as const;
export type Reason = (typeof REASONS)[number];

/** Правила без денег: сигнал процесса или учёта, а не переплата. */
export const isSignal = (f: Pick<Finding, "overpayment">) => Number(f.overpayment) <= 0;

type Evidence = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : undefined);

// «Арматура ⌀14» → «арматура ⌀14», но «ГКЛ 12,5» остаётся как есть
const lower = (s: string) => (s.length > 1 && s[1] === s[1]!.toLowerCase() ? s[0]!.toLowerCase() + s.slice(1) : s);

/** Заголовок-вопрос (§ 8.3 ТЗ). Для списка evidence не приходит — берём то, что есть. */
export function questionTitle(f: Finding & { evidence?: Evidence }): string {
  const ev = f.evidence ?? {};
  const sku = f.sku_label ?? "позиция";
  const who = f.supplier ?? "поставщика";
  const premium = str(ev.premium_median) ?? premiumFromMessage(f.message);
  switch (f.code) {
    case "P-01": {
      const cheaper = (ev.cheaper_suppliers as string[] | undefined)?.[0] ?? cheaperFromMessage(f.message);
      const than = cheaper ? `, чем у ${cheaper}` : ", чем у других поставщиков";
      return premium
        ? `Почему ${lower(sku)} у ${who} на ${share(premium)} дороже${than}?`
        : `Почему ${lower(sku)} у ${who} дороже${than}?`;
    }
    case "P-02":
      return `Почему цена ${who} на ${lower(sku)} растёт быстрее рынка?`;
    case "P-03":
      return premium ? `Почему ${lower(sku)} у ${who} на ${share(premium)} дороже рынка?` : `Почему ${lower(sku)} у ${who} дороже рынка?`;
    case "P-04": {
      const s = str(ev.share);
      return `Почему ${s ? share(s) + " " : ""}закупок «${sku}» — у одного поставщика, который дороже?`;
    }
    case "P-05":
      return `Почему закупку «${sku}» разбили на части ниже порога согласования?`;
    case "P-06": {
      const m = /№\s*(\S+)\s+от\s+(\d{2}\.\d{2}\.\d{4})/.exec(f.message);
      return m ? `Документ ${m[1]} от ${m[2]} учтён дважды?` : "Документ учтён дважды?";
    }
    case "P-07":
      return `Цена «${sku}» не сходится с единицей — ошибка в учёте?`;
    default:
      return f.title;
  }
}

/** Объяснение от движка без хвоста «переплата N сом» — сумма и так крупно рядом. */
export const explanation = (message: string) => message.replace(/[;,]?\s*переплата\s+[\d\s\u00a0]+сом\s*$/u, "").trim() + ".";

// для списка: из message «…в среднем на 16,8% дороже, чем в те же недели у ОсОО «Темир Снаб»…»
function premiumFromMessage(message: string): string | undefined {
  const m = /на\s+(\d+(?:,\d+)?)%/.exec(message);
  return m ? String(Number(m[1]!.replace(",", ".")) / 100) : undefined;
}
function cheaperFromMessage(message: string): string | undefined {
  const m = /в те же недели у (.+?);/.exec(message);
  return m?.[1];
}

export const meta = (f: Finding) =>
  [f.sku_label, f.supplier ?? "несколько поставщиков", f.responsible ?? "ответственный не указан"].filter(Boolean).join(" · ");
