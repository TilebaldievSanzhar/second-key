// Деньги приходят строками-десятичными («125000.00»): считаем на сервере, здесь только показываем.
const som = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const price = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const day = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

export const money = (v: string | number | null | undefined) =>
  v === null || v === undefined ? "—" : `${som.format(Number(v))} сом`;

export const num = (v: string | number | null | undefined) =>
  v === null || v === undefined ? "—" : price.format(Number(v));

export const date = (v: string | null | undefined) => (v ? day.format(new Date(v)) : "—");

export const pct = (part: string | number, whole: string | number) => {
  const w = Number(whole);
  return w ? `${((Number(part) / w) * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%` : "—";
};

export const UNIT: Record<string, string> = {
  t: "т", kg: "кг", m3: "м³", m2: "м²", m: "м", pcs: "шт", l: "л", sheet: "лист", bag: "мешок", pack: "упак",
};

export const STATUS_LABEL: Record<string, string> = {
  open: "Ждёт ответа",
  answered: "Есть ответ",
  confirmed: "Переплата подтверждена",
  justified: "Обосновано",
  data_error: "Ошибка учёта",
};

export const RUN_STAGE: Record<string, string> = {
  queued: "В очереди",
  importing: "Читаем файл",
  normalizing: "Сводим номенклатуру",
  analyzing: "Сравниваем цены",
  reporting: "Готовим отчёт",
  done: "Готово",
  failed: "Ошибка",
};
