// Язык вопросов: правила словами, заголовок-вопрос из полей находки, статусы двух ключей.
// Интерфейс задаёт вопрос, а не обвиняет (docs/design/FRONTEND_SPEC.md § 8).
import type { Finding, FindingStatus, Trace } from "./api/client";
import { share } from "./format";

export const RULE_TEXT: Record<string, string> = {
  "P-01": "Дороже, чем у другого поставщика в те же недели",
  "P-02": "Цена поставщика растёт быстрее рынка",
  "P-03": "Дороже рынка",
  "P-04": "Почти всё у одного, и он дороже",
  "P-05": "Дробление закупки ниже порога согласования",
  "P-06": "Документ учтён дважды",
  "P-07": "Цена не сходится с единицей измерения",
  LINK: "Связка снабженца и поставщика",
};

export const STATUS_TEXT: Record<FindingStatus, string> = {
  open: "Ждёт ответа",
  answered: "Есть ответ",
  confirmed: "Переплата подтверждена",
  justified: "Обосновано",
  data_error: "Ошибка учёта",
};

/** Статус словами; у связки «подтверждено» — сама связка, а не переплата. */
export const statusText = (s: FindingStatus, code?: string) =>
  code === "LINK" && s === "confirmed" ? "Связка подтверждена" : STATUS_TEXT[s];

export const WAITING: FindingStatus[] = ["open", "answered"];
export const DECIDED: FindingStatus[] = ["confirmed", "justified", "data_error"];
export const isDecided = (s: FindingStatus) => DECIDED.includes(s);

export const DECISIONS = [
  { status: "confirmed", label: "Переплата подтверждена", key: "1" },
  { status: "justified", label: "Обосновано", key: "2" },
  { status: "data_error", label: "Ошибка учёта", key: "3" },
] as const;
export type Decision = (typeof DECISIONS)[number]["status"];

// связка — вопрос о выборе поставщика: денег она не добавляет, подтверждается сама связка
const LINK_DECISIONS = [
  { status: "confirmed", label: "Связка подтверждена", key: "1" },
  { status: "justified", label: "Обосновано", key: "2" },
  { status: "data_error", label: "Ошибка учёта", key: "3" },
] as const;
export const decisionsFor = (code: string) => (code === "LINK" ? LINK_DECISIONS : DECISIONS);

// --- связки снабженца и поставщика (engine/links.py) ---------------------------------------------

export type TraceGroup = "price" | "behavior" | "identity";
export const GROUPS: { key: TraceGroup; label: string; hint: string }[] = [
  { key: "price", label: "Цена", hint: "переплата держится через этого снабженца" },
  { key: "behavior", label: "Поведение", hint: "как поставщик появился и закрепился" },
  { key: "identity", label: "Поставщик", hint: "что известно о самом поставщике" },
];

/** Короткие названия следов — заголовок над текстом следа. */
export const TRACE_TEXT: Record<string, string> = {
  "L-01": "Дороже, чем покупают коллеги",
  "L-04": "Почти всё — через одного снабженца",
  "L-05": "Пришёл вместе с новым снабженцем",
  "L-06": "Вытеснил прежнего поставщика",
  "L-09": "Вы — почти единственный клиент",
  "L-10": "Возраст по номеру ИНН",
  "L-11": "Общие реквизиты с другим поставщиком",
  "L-12": "ИНН совпадает с ИНН сотрудника",
  "P-01": "Дороже другого поставщика в те же недели",
  "P-02": "Цена растёт быстрее рынка",
  "P-03": "Дороже рынка",
};

const num = (v: unknown) => (typeof v === "number" ? v : typeof v === "string" && v !== "" ? Number(v) : NaN);
const years = (days: number) => {
  const y = Math.floor(days / 365.25);
  const w = y % 10 === 1 && y % 100 !== 11 ? "год" : y % 10 >= 2 && y % 10 <= 4 && (y % 100 < 12 || y % 100 > 14) ? "года" : "лет";
  return `${y} ${w}`;
};

/** Подписи следов группы: одинаковые следы по разным позициям сливаются в одну строку. */
export function traceSummaries(traces: Trace[]): string[] {
  const byCode = new Map<string, Trace[]>();
  for (const t of traces) byCode.set(t.code, [...(byCode.get(t.code) ?? []), t]);
  return [...byCode.entries()].map(([code, ts]) => {
    if (ts.length === 1) return traceSummary(ts[0]!);
    const f = ts.map((t) => (t.facts ?? {}) as Record<string, unknown>);
    if (code === "L-06") {
      const who = [...new Set(f.map((x) => x.displaced).filter((x): x is string => typeof x === "string"))].join(", ");
      const p = f.map((x) => num(x.premium)).filter(Number.isFinite);
      const range = p.length ? `, дороже на ${share(Math.min(...p))}–${share(Math.max(...p))}` : "";
      return `Вытеснил ${who || "прежних поставщиков"} по ${ts.length} позициям${range}`;
    }
    if (code === "L-11") return `Общие реквизиты с ${f.map((x) => x.other).filter(Boolean).join(", ")}`;
    return `${traceSummary(ts[0]!)} (и ещё ${ts.length - 1})`;
  });
}

/** Подпись следа одной строкой: название и главное число из facts. */
export function traceSummary(t: Trace): string {
  const f = (t.facts ?? {}) as Record<string, unknown>;
  switch (t.code) {
    case "L-01":
      return Number.isFinite(num(f.premium_median)) ? `Дороже, чем покупают коллеги, на ${share(num(f.premium_median))}` : TRACE_TEXT[t.code]!;
    case "L-04":
      return Number.isFinite(num(f.share)) ? `${share(num(f.share))} закупок — через одного снабженца` : TRACE_TEXT[t.code]!;
    case "L-05":
      return Number.isFinite(num(f.lag_days)) ? `Пришёл через ${num(f.lag_days)} дн. после нового снабженца` : TRACE_TEXT[t.code]!;
    case "L-06":
      return `Вытеснил ${typeof f.displaced === "string" ? f.displaced : "прежнего поставщика"}${Number.isFinite(num(f.premium)) ? `, дороже на ${share(num(f.premium))}` : ""}`;
    case "L-09":
      return Number.isFinite(num(f.ours)) ? `Номера счетов подряд: ${num(f.ours)} из ${num(f.span)} — ваши` : TRACE_TEXT[t.code]!;
    case "L-10": {
      const d = num(f.days_before_first);
      if (!Number.isFinite(d)) return TRACE_TEXT[t.code]!;
      return f.inn_kind === "org"
        ? `Зарегистрирована за ${Math.max(1, Math.round(d / 30.4))} мес. до первой поставки`
        : `ИП — ${years(d)} по номеру ИНН`;
    }
    case "L-11":
      return typeof f.other === "string" ? `Общие реквизиты с ${f.other}` : TRACE_TEXT[t.code]!;
    case "L-12":
      return typeof f.staff === "string" ? `ИНН сотрудника: ${f.staff}${typeof f.role === "string" ? `, ${f.role}` : ""}` : TRACE_TEXT[t.code]!;
    default:
      return TRACE_TEXT[t.code] ?? t.code;
  }
}

export const REASONS = [
  "Срочная поставка",
  "Доставка включена в цену",
  "Другая марка или качество",
  "Отсрочка платежа",
  "Ошибка в учёте",
  "Другое",
] as const;
export type Reason = (typeof REASONS)[number];

/** Правила без денег: сигнал процесса или учёта, а не переплата. Связка денег не добавляет — её сумма справочно
 * повторяет переплату ценовых вопросов по тем же строкам (engine/links.py), поэтому она тоже сигнал. */
export const isSignal = (f: Pick<Finding, "overpayment" | "code">) => f.code === "LINK" || Number(f.overpayment) <= 0;

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
    case "LINK":
      // вопрос о выборе поставщика, без выводов: его видит и снабженец
      return f.sku_label ? `Как выбрали ${who} для закупок «${lower(f.sku_label)}»?` : `Как выбрали ${who}?`;
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
