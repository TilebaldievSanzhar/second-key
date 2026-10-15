// Форматы чисел, денег и дат. Деньги приходят строками-десятичными («125000.00»):
// считает бэкенд, здесь только показываем. Пробел-разделитель тысяч — неразрывный.

type Num = string | number | null | undefined;

const NBSP = "\u00a0";
const fmt = (digits: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
const f0 = fmt(0);
const f1 = fmt(1);
const f2 = fmt(2);
const f3 = fmt(3);
const nb = (s: string) => s.replace(/[\u202f\u00a0 ]/g, NBSP);

export const isNum = (v: Num): v is string | number => v !== null && v !== undefined && v !== "";

/** 8 267 401 */
export const int = (v: Num) => (isNum(v) ? nb(f0.format(Number(v))) : "—");

/** 8 267 401 сом */
export const money = (v: Num) => (isNum(v) ? `${int(v)}${NBSP}сом` : "—");

/** 8,3 млн сом · 269 млн сом · 612 480 сом */
export function moneyShort(v: Num): string {
  if (!isNum(v)) return "—";
  const n = Number(v);
  if (Math.abs(n) >= 1e9) return `${nb((n >= 1e10 ? f0 : f1).format(n / 1e9))}${NBSP}млрд сом`;
  if (Math.abs(n) >= 1e6) return `${nb((n >= 1e8 ? f0 : f1).format(n / 1e6))}${NBSP}млн сом`;
  return money(n);
}

/** Количество и цены за единицу: до 3 знаков у количества, до 2 у цен. */
export const qty = (v: Num) => (isNum(v) ? nb(f3.format(Number(v))) : "—");
export const price = (v: Num) => (isNum(v) ? nb(f2.format(Number(v))) : "—");

/** Доля «0.168» → «16,8%». */
export const share = (v: Num) => (isNum(v) ? `${nb(f1.format(Number(v) * 100))}%` : "—");

/** Часть от целого → «3,1%». */
export const pct = (part: Num, whole: Num) =>
  isNum(part) && isNum(whole) && Number(whole) ? `${nb(f1.format((Number(part) / Number(whole)) * 100))}%` : "—";

const pad = (n: number) => String(n).padStart(2, "0");
const toDate = (v: string) => (v.length === 10 ? new Date(`${v}T00:00:00`) : new Date(v));

/** 17.01.2026 */
export const date = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = toDate(v);
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
};

/** 17.01 */
export const dayMonth = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = toDate(v);
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
};

/** 14.10.2026, 18:42 */
export const dateTime = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = new Date(v);
  return `${date(v)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

/** 15.10, 12:53 */
export const dayTime = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = new Date(v);
  return `${dayMonth(v)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** «Окт 2025 — сен 2026» для селектора периода. */
export function period(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "Период не определён";
  const a = toDate(start);
  const b = toDate(end);
  const s = `${MONTHS[a.getMonth()]} ${a.getFullYear()} — ${MONTHS[b.getMonth()]} ${b.getFullYear()}`;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** plural(3, ["строка", "строки", "строк"]) → «строки». */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

export const count = (n: number, forms: [string, string, string]) => `${int(n)}${NBSP}${plural(n, forms)}`;

export const LINES: [string, string, string] = ["строка", "строки", "строк"];
export const QUESTIONS: [string, string, string] = ["вопрос", "вопроса", "вопросов"];
export const PURCHASES: [string, string, string] = ["покупка", "покупки", "покупок"];

export const UNIT: Record<string, string> = {
  t: "т",
  kg: "кг",
  m3: "м³",
  m2: "м²",
  m: "м",
  pcs: "шт",
  l: "л",
  sheet: "лист",
  bag: "мешок",
  pack: "упак",
};
export const unit = (u: string | null | undefined) => (u ? (UNIT[u] ?? u) : "");

/** Цена за единицу в шапке графика: «тонну», «м³»… */
export const PER_UNIT: Record<string, string> = { t: "тонну", kg: "килограмм", m3: "м³", m2: "м²", m: "метр", pcs: "штуку", l: "литр", sheet: "лист", bag: "мешок", pack: "упаковку" };

/** «Поступление товаров и услуг 0002388 от 17.01.2026» → «0002388». */
export function docNumber(doc: string | null | undefined): string {
  if (!doc) return "—";
  const m = /(\d{4,})/.exec(doc);
  return m ? m[1]! : doc;
}

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");

export const fileSize = (bytes: number) =>
  bytes >= 1024 * 1024 ? `${nb(f1.format(bytes / 1024 / 1024))}${NBSP}МБ` : `${int(Math.max(1, bytes / 1024))}${NBSP}КБ`;
