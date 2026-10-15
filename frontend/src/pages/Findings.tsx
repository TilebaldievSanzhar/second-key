// Входящие вопросы: крупные сверху, табы по ключам, фильтры-чипы в адресе страницы, J/K с клавиатуры.
// Снабжение видит тот же список, но только свои вопросы и разбитый по тому, что ждёт от него.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import type { Finding, FindingStatus } from "../api/client";
import { FindingRow } from "../components/FindingRow";
import { Icon } from "../components/Icon";
import { RunPicker } from "../components/RunPicker";
import { Empty, ErrorNote, Skeleton } from "../components/State";
import { QUESTIONS, count, moneyShort } from "../format";
import { DECIDED, RULE_TEXT, WAITING } from "../questions";
import { canSeeAll, useCurrentRun, useRole, useRunFindings } from "../session";
import { MobileHead } from "./Layout";

const TABS: { key: string; label: string; status: FindingStatus[] }[] = [
  { key: "waiting", label: "Ждут решения", status: WAITING },
  { key: "decided", label: "Решённые", status: DECIDED },
  { key: "all", label: "Все", status: [] },
];
const FILTERS = [
  { key: "responsible", label: "Ответственный" },
  { key: "code", label: "Правило" },
  { key: "supplier", label: "Поставщик" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

export function FindingsPage() {
  const role = useRole();
  return canSeeAll(role) ? <Inbox /> : <MyQuestions />;
}

function Inbox() {
  const { run, loading } = useCurrentRun();
  const q = useRunFindings(run?.id);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = TABS.find((t) => t.key === params.get("tab")) ?? TABS[0]!;
  const filters = Object.fromEntries(FILTERS.map((f) => [f.key, params.get(f.key)])) as Record<FilterKey, string | null>;
  const all = useMemo(() => q.data ?? [], [q.data]);

  const filtered = all.filter(
    (f) =>
      (!filters.responsible || f.responsible === filters.responsible) &&
      (!filters.code || f.code === filters.code) &&
      (!filters.supplier || f.supplier === filters.supplier),
  );
  const shown = filtered.filter((f) => !tab.status.length || tab.status.includes(f.status));
  const counts = Object.fromEntries(
    TABS.map((t) => [t.key, filtered.filter((f) => !t.status.length || t.status.includes(f.status)).length]),
  );
  const [sel, setSel] = useState(-1);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.key === "j") setSel((i) => Math.min(i + 1, shown.length - 1));
      else if (e.key === "k") setSel((i) => Math.max(i - 1, 0));
      else if (e.key === "Enter" && shown[sel]) navigate(`/findings/${shown[sel].id}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, sel, navigate]);

  const setParam = (key: string, value: string | null) =>
    setParams((p) => {
      if (value) p.set(key, value);
      else p.delete(key);
      return p;
    });

  const options: Record<FilterKey, { value: string; label: string }[]> = {
    responsible: uniq(all.map((f) => f.responsible)).map((v) => ({ value: v, label: v })),
    code: uniq(all.map((f) => f.code))
      .sort()
      .map((v) => ({ value: v, label: RULE_TEXT[v] ?? v })),
    supplier: uniq(all.map((f) => f.supplier)).map((v) => ({ value: v, label: v })),
  };
  const anyFilter = FILTERS.some((f) => filters[f.key]);

  return (
    <section className="page">
      <MobileHead>
        <span className="mhead-title">Вопросы</span>
        <RunPicker />
      </MobileHead>
      <header className="page-head">
        <h1 className="page-title">Вопросы</h1>
        <div className="page-actions">
          <RunPicker variant="boxed" />
        </div>
      </header>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === tab.key}
            className={t.key === tab.key ? "on" : ""}
            onClick={() => setParam("tab", t.key === "waiting" ? null : t.key)}
          >
            {t.label}
            <span className="tab-count">{counts[t.key]}</span>
          </button>
        ))}
      </div>

      <div className="chips" aria-label="Фильтры">
        {FILTERS.map((f) =>
          filters[f.key] ? (
            <button key={f.key} type="button" className="chip chip-on" onClick={() => setParam(f.key, null)} aria-label={`Сбросить фильтр «${f.label}»`}>
              {f.key === "code" ? RULE_TEXT[filters.code!] ?? filters.code : filters[f.key]}
              <Icon name="close" size={14} strokeWidth={2} />
            </button>
          ) : (
            <label key={f.key} className="chip">
              {f.label}
              <Icon name="down" size={14} strokeWidth={2} />
              <select value="" aria-label={f.label} onChange={(e) => setParam(f.key, e.target.value || null)}>
                <option value="">{f.label}: все</option>
                {options[f.key].map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          ),
        )}
      </div>

      {loading || q.isPending ? (
        <Skeleton lines={8} />
      ) : q.isError ? (
        <ErrorNote error={q.error} retry={() => q.refetch()} />
      ) : !run ? (
        <Empty title="Проверок ещё не было" action={<Link to="/uploads" className="btn btn-primary">Загрузить выгрузку</Link>} />
      ) : shown.length === 0 ? (
        anyFilter ? (
          <Empty
            title="По этому фильтру ничего"
            action={
              <button type="button" className="btn btn-secondary" onClick={() => setParams({})}>
                Сбросить фильтры
              </button>
            }
          />
        ) : tab.key === "waiting" ? (
          <Empty
            title="Все вопросы разобраны"
            action={
              <button type="button" className="btn btn-secondary" onClick={() => setParam("tab", "decided")}>
                Открыть решённые
              </button>
            }
          >
            {doneLine(all)}
          </Empty>
        ) : (
          <Empty title="Здесь пока пусто" />
        )
      ) : (
        <>
          <p className="list-note">
            Сначала крупные · {shown.length} из {all.length}
            {anyFilter ? " по фильтру" : ""}
            <span className="desktop-only"> · J/K — по списку, Enter — открыть</span>
          </p>
          <ul className="flist">
            {shown.map((f, i) => (
              <FindingRow key={f.id} f={f} showRule selected={i === sel} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function MyQuestions() {
  const { run, loading } = useCurrentRun();
  const q = useRunFindings(run?.id);
  const items = q.data ?? [];
  const groups: { title: string; items: Finding[] }[] = [
    { title: "Ждут вашего ответа", items: items.filter((f) => f.status === "open") },
    { title: "Отвечены — решение за собственником", items: items.filter((f) => f.status === "answered") },
    { title: "Решены", items: items.filter((f) => DECIDED.includes(f.status)) },
  ];
  return (
    <section className="page">
      <MobileHead>
        <span className="mhead-title">Мои вопросы</span>
      </MobileHead>
      <header className="page-head">
        <h1 className="page-title">Мои вопросы</h1>
      </header>
      <p className="note note-accent">
        Система сравнивает цены автоматически. Здесь — вопросы по вашим закупкам: объясните, почему цена такая, — решение
        примет собственник.
      </p>
      {loading || q.isPending ? (
        <Skeleton lines={6} />
      ) : q.isError ? (
        <ErrorNote error={q.error} retry={() => q.refetch()} />
      ) : items.length === 0 ? (
        <Empty title="Вопросов по вашим закупкам нет">Когда проверка найдёт что-то по вашим закупкам, вопрос появится здесь.</Empty>
      ) : (
        groups
          .filter((g) => g.items.length)
          .map((g) => (
            <section key={g.title} className="block">
              <h2 className="block-title">
                {g.title} <span className="muted">{g.items.length}</span>
              </h2>
              <ul className="flist">
                {g.items.map((f) => (
                  <FindingRow key={f.id} f={f} />
                ))}
              </ul>
            </section>
          ))
      )}
    </section>
  );
}

const uniq = (xs: (string | null | undefined)[]) => [...new Set(xs.filter((x): x is string => !!x))].sort((a, b) => a.localeCompare(b, "ru"));

function doneLine(all: Finding[]) {
  const confirmed = all.filter((f) => f.status === "confirmed").reduce((a, f) => a + Number(f.overpayment), 0);
  const justified = all.filter((f) => f.status === "justified").length;
  return `Подтверждено ${moneyShort(confirmed)}, обосновано ${count(justified, QUESTIONS)}.`;
}
