// Новая проверка: файл → колонки → проверка. Прогресс — по SSE, страницу можно закрыть: проверка идёт в фоне.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, unwrap, type Run, type Upload } from "../api/client";
import { Icon } from "../components/Icon";
import { StageProgress } from "../components/StageProgress";
import { Empty, ErrorNote, Notice, Skeleton } from "../components/State";
import { useToast } from "../components/Toast";
import { LINES, QUESTIONS, count, date, dayTime, fileSize, int, moneyShort } from "../format";
import { canUpload, useCurrentRun, useRole, useRuns } from "../session";
import { MobileHead } from "./Layout";

const FIELDS: { key: string; label: string }[] = [
  { key: "date", label: "Дата" },
  { key: "doc_no", label: "Документ" },
  { key: "supplier", label: "Поставщик" },
  { key: "supplier_inn", label: "ИНН поставщика" },
  { key: "item", label: "Номенклатура" },
  { key: "unit", label: "Ед. изм." },
  { key: "qty", label: "Количество" },
  { key: "price", label: "Цена" },
  { key: "amount", label: "Сумма" },
  { key: "vat", label: "НДС" },
  { key: "object_name", label: "Объект" },
  { key: "responsible", label: "Ответственный" },
];
const LABEL = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));
const REQUIRED: { label: string; ok: (m: Record<string, number>) => boolean }[] = [
  { label: "Дата", ok: (m) => "date" in m },
  { label: "Поставщик", ok: (m) => "supplier" in m },
  { label: "Номенклатура", ok: (m) => "item" in m },
  { label: "Количество", ok: (m) => "qty" in m },
  { label: "Цена или сумма", ok: (m) => "price" in m || "amount" in m },
];
const TERMINAL = ["done", "failed"];
const MAX_MB = 25;

type Step = "file" | "columns" | "check";

export function UploadsPage() {
  const role = useRole();
  const runs = useRuns();
  const running = runs.data?.items.find((r) => !TERMINAL.includes(r.status));
  const [step, setStep] = useState<Step>(running ? "check" : "file");
  const [source, setSource] = useState<"file" | "1c">("file");
  const [upload, setUpload] = useState<Upload | null>(null);
  const [runId, setRunId] = useState<string | null>(running?.id ?? null);
  const activeRun = runId ?? running?.id ?? null;
  const reset = () => {
    setUpload(null);
    setRunId(null);
    setStep("file");
  };

  return (
    <section className="page">
      <MobileHead>
        <span className="mhead-title">Загрузки</span>
      </MobileHead>
      {canUpload(role) && (
        <>
          <header className="page-head">
            <div>
              <h1 className="page-title">Новая проверка</h1>
              <p className="page-sub">Выгрузка закупок из 1С за 6–12 месяцев</p>
            </div>
          </header>
          <ol className="steps">
            {(["file", "columns", "check"] as Step[]).map((s, i) => {
              const order = ["file", "columns", "check"].indexOf(step);
              const st = i < order ? "done" : i === order ? "now" : "todo";
              return (
                <li key={s} className={`step step-${st}`} aria-current={st === "now" ? "step" : undefined}>
                  <span className="step-dot">{st === "done" ? <Icon name="check" size={12} strokeWidth={3} /> : i + 1}</span>
                  {["Файл", "Колонки", "Проверка"][i]}
                </li>
              );
            })}
          </ol>
          {step === "file" && (
            <div className="seg source-tabs" role="tablist" aria-label="Источник">
              <button type="button" role="tab" aria-selected={source === "file"} className={source === "file" ? "on" : ""} onClick={() => setSource("file")}>
                Файл Excel или CSV
              </button>
              <button type="button" role="tab" aria-selected={source === "1c"} className={source === "1c" ? "on" : ""} onClick={() => setSource("1c")}>
                Из 1С
              </button>
            </div>
          )}
          {step === "file" && source === "1c" && <OneCSource onQueued={() => setStep("file")} />}
          {step === "file" && source === "file" && (
            <FileStep
              onUploaded={(u) => {
                setUpload(u);
                setStep("columns");
              }}
            />
          )}
          {step === "columns" && upload && (
            <ColumnsStep
              upload={upload}
              onReplace={reset}
              onStarted={(id) => {
                setRunId(id);
                setStep("check");
              }}
            />
          )}
          {step === "check" && activeRun && <CheckStep runId={activeRun} onAgain={reset} />}
        </>
      )}
      <PastRuns runs={runs.data?.items} loading={runs.isPending} error={runs.error} />
    </section>
  );
}

function FileStep({ onUploaded }: { onUploaded: (u: Upload) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      // multipart: тело передаём как есть, границу проставит браузер
      return unwrap(api.POST("/api/uploads", { body: body as never, bodySerializer: (b) => b as unknown as FormData }));
    },
    onSuccess: onUploaded,
  });
  const pick = (file: File | undefined) => {
    if (!file) return;
    setLocalError(null);
    if (!/\.(xlsx|xlsm|csv)$/i.test(file.name)) return setLocalError("Нужен файл Excel (.xlsx) или CSV — выгрузка закупок из 1С или таблица");
    if (file.size > MAX_MB * 1024 * 1024)
      return setLocalError(`Файл больше ${MAX_MB} МБ — выгрузите период короче или без лишних колонок`);
    send.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    pick(e.dataTransfer.files[0]);
  };
  return (
    <div className="file-step">
      <div
        className={`dropzone${over ? " dropzone-over" : ""}${send.isPending ? " dropzone-busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <Icon name="sheet" size={28} />
        {send.isPending ? (
          <span className="dropzone-title">Читаем файл…</span>
        ) : (
          <>
            <span className="dropzone-title">Перетащите выгрузку сюда</span>
            <span className="muted small">.xlsx, .xlsm или .csv, до {MAX_MB} МБ</span>
            <button type="button" className="btn btn-primary" onClick={() => input.current?.click()}>
              Выбрать файл
            </button>
          </>
        )}
        <input ref={input} type="file" accept=".xlsx,.xlsm,.csv" hidden onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      <div className="howto">
        <h3>Как выгрузить из 1С</h3>
        <ol>
          <li>Отчёты → Закупки → «Поступление товаров и услуг».</li>
          <li>Детализация — по строкам товаров, период — 6–12 месяцев.</li>
          <li>Колонки: дата, документ, контрагент, номенклатура, количество, цена или сумма; объект и ответственный — если есть.</li>
          <li>Сохранить → Лист Excel (.xlsx).</li>
        </ol>
      </div>
      {localError && (
        <p className="inline-error" role="alert">
          <Icon name="alert" size={18} strokeWidth={2} />
          {localError}
        </p>
      )}
      {send.error && <ErrorNote error={send.error} />}
    </div>
  );
}

function ColumnsStep({ upload, onReplace, onStarted }: { upload: Upload; onReplace: () => void; onStarted: (runId: string) => void }) {
  const p = upload.preview;
  const qc = useQueryClient();
  // шапку не нашли — человек выбирает строку с заголовками среди первых строк файла
  // сопоставление «как в прошлый раз» для такой же шапки или подсказка ИИ для нестандартного файла
  const sug = p.suggested;
  const [headerRow, setHeaderRow] = useState(p.header_row || sug?.header_row || 0);
  const columns = p.header_row ? p.columns : headerRow ? (p.sample[headerRow - 1] ?? []) : [];
  const rows = p.header_row ? p.sample.slice(0, 5) : headerRow ? p.sample.slice(headerRow, headerRow + 5) : [];
  const [mapping, setMapping] = useState<Record<string, number>>(sug ? { ...sug.mapping } : p.header_row ? { ...p.mapping } : {});
  const byCol = useMemo(() => Object.fromEntries(Object.entries(mapping).map(([f, c]) => [c, f])), [mapping]);
  const missing = REQUIRED.filter((r) => !r.ok(mapping));

  const assign = (col: number, field: string) =>
    setMapping((m) => {
      const next = Object.fromEntries(Object.entries(m).filter(([f, c]) => c !== col && f !== field));
      if (field) next[field] = col;
      return next;
    });

  const start = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/audits", { body: { upload_id: upload.id, mapping, header_row: headerRow || null } })),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      onStarted(run.id);
    },
  });

  return (
    <div className="columns-step">
      <div className="filecard">
        <Icon name="sheet" size={22} />
        <div className="filecard-main">
          <span className="filecard-name">{upload.filename}</span>
          <span className="muted small">
            {fileSize(upload.size)} · {p.header_row ? `заголовок в строке ${p.header_row}` : "заголовок не найден"} · {count(p.data_rows ?? 0, LINES)} ·{" "}
            {columns.length} колонок
          </span>
        </div>
        <button type="button" className="btn btn-link" onClick={onReplace}>
          Заменить файл
        </button>
      </div>

      {sug?.by === "saved" && <Notice tone="accent">Колонки сопоставлены как в прошлый раз для этой формы отчёта.</Notice>}
      {sug?.by === "ai" && (
        <Notice tone="accent">
          Колонки предложил ИИ — проверьте их перед запуском.{sug.notes ? ` ${sug.notes}` : ""}
        </Notice>
      )}
      {!p.header_row && (
        <Notice>
          Не нашли строку с заголовками.{" "}
          <label className="inline-select">
            Заголовки в строке
            <select value={headerRow} onChange={(e) => setHeaderRow(Number(e.target.value))}>
              <option value={0}>выберите…</option>
              {p.sample.map((r, i) => (
                <option key={i} value={i + 1}>
                  {i + 1}: {r.filter(Boolean).slice(0, 3).join(" · ").slice(0, 60)}
                </option>
              ))}
            </select>
          </label>
        </Notice>
      )}
      {p.header_row > 0 && missing.length > 0 && (
        <Notice>
          Файл прочитан, но не нашли: {missing.map((m) => m.label.toLowerCase()).join(", ")}. Укажите колонки в таблице ниже.
        </Notice>
      )}

      {columns.length > 0 && (
        <div>
          <p className="muted small">Первые строки файла. Над каждой колонкой — что мы в ней нашли.</p>
          <div className="mapper" role="table" aria-label="Сопоставление колонок">
            {columns.map((name, col) => {
              const field = byCol[col] ?? "";
              return (
                <div key={col} className={`mcol${field ? "" : " mcol-free"}`} role="rowgroup">
                  <div className="mcol-head">
                    <label className="mselect">
                      <span>{field ? LABEL[field] : "—"}</span>
                      <Icon name="down" size={14} strokeWidth={2} />
                      <select aria-label={`Колонка «${name || col + 1}»`} value={field} onChange={(e) => assign(col, e.target.value)}>
                        <option value="">— не использовать</option>
                        {FIELDS.map((f) => (
                          <option key={f.key} value={f.key}>
                            {f.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span className="mcol-name">{name || `колонка ${col + 1}`}</span>
                  </div>
                  {rows.map((r, i) => (
                    <div key={i} className="mcell" title={r[col] ?? ""}>
                      {r[col] ?? ""}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="required">
        <div className="required-list">
          <span className="required-title">Обязательные поля</span>
          {REQUIRED.map((r) => {
            const ok = r.ok(mapping);
            return (
              <span key={r.label} className={`req req-${ok ? "ok" : "miss"}`}>
                <span className="req-dot">{ok ? <Icon name="check" size={11} strokeWidth={3.5} /> : "!"}</span>
                {r.label}
              </span>
            );
          })}
        </div>
        <div className="required-go">
          <button type="button" className="btn btn-primary btn-lg" disabled={missing.length > 0 || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Ставим в очередь…" : "Колонки верны — проверить закупки"}
          </button>
          <span className="muted small">
            {missing.length ? `Укажите: ${missing.map((m) => m.label.toLowerCase()).join(", ")}. ` : ""}Проверка идёт в фоне 5–60 секунд.
          </span>
        </div>
      </div>
      {start.error && <ErrorNote error={start.error} />}
    </div>
  );
}

function CheckStep({ runId, onAgain }: { runId: string; onAgain: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { select } = useCurrentRun();
  const [ev, setEv] = useState<{ status: string; progress: number; error?: string | null }>({ status: "queued", progress: 0 });
  const [lastStage, setLastStage] = useState("queued");
  const run = useQuery({
    queryKey: ["run", runId],
    enabled: TERMINAL.includes(ev.status),
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}", { params: { path: { run_id: runId } } })),
  });
  const [attempt, setAttempt] = useState(0);
  const retry = useMutation({
    mutationFn: () => unwrap(api.POST("/api/audits/{run_id}/retry", { params: { path: { run_id: runId } } })),
    onSuccess: () => {
      setEv({ status: "queued", progress: 0 });
      setAttempt((a) => a + 1);
    },
  });

  // первое событие — состояние из БД, дальше — смена стадий до done/failed
  useEffect(() => {
    const es = new EventSource(`/api/audits/${runId}/events`, { withCredentials: true });
    es.addEventListener("progress", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { status: string; progress: number; error?: string | null };
      setEv(data);
      if (!TERMINAL.includes(data.status)) setLastStage(data.status);
      if (TERMINAL.includes(data.status)) {
        es.close();
        qc.invalidateQueries({ queryKey: ["runs"] });
        qc.invalidateQueries({ queryKey: ["run", runId] });
      }
    });
    return () => es.close();
  }, [runId, attempt, qc]);

  const r = run.data;
  const share = r
    ? `https://wa.me/?text=${encodeURIComponent(
        `Проверка закупок готова: возможная переплата ${moneyShort(r.overpayment)}, вопросов ${r.findings_count}. ${window.location.origin}/`,
      )}`
    : "#";
  return (
    <div className="check-step">
      <div className="panel">
        <StageProgress status={ev.status} progress={ev.progress} failedAt={lastStage} />
        {ev.status === "failed" && (
          <>
            <p className="inline-error">
              <Icon name="alert" size={18} strokeWidth={2} />
              {ev.error ?? r?.error ?? "Проверка остановилась"}
            </p>
            <div className="actions">
              <button type="button" className="btn btn-primary" onClick={() => retry.mutate()} disabled={retry.isPending}>
                Запустить ещё раз
              </button>
              <button type="button" className="btn btn-secondary" onClick={onAgain}>
                Заменить файл
              </button>
            </div>
          </>
        )}
        {ev.status === "done" && r && (
          <>
            <p className="result">
              Проверено {count(r.lines_total, LINES)} · {count(r.findings_count, QUESTIONS)} · возможная переплата{" "}
              <strong className="over">{moneyShort(r.overpayment)}</strong>
            </p>
            <div className="actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  select(r.id);
                  navigate("/");
                }}
              >
                Открыть сводку
              </button>
              <a className="btn btn-secondary" href={share} target="_blank" rel="noreferrer">
                Отправить собственнику
              </a>
              <button type="button" className="btn btn-link" onClick={onAgain}>
                Загрузить ещё файл
              </button>
            </div>
          </>
        )}
        {!TERMINAL.includes(ev.status) && <p className="muted small">Проверка идёт в фоне: страницу можно закрыть, результат появится в списке.</p>}
      </div>
    </div>
  );
}

function PastRuns({ runs, loading, error }: { runs?: Run[]; loading: boolean; error: unknown }) {
  const navigate = useNavigate();
  const { select } = useCurrentRun();
  const open = (r: Run) => {
    if (r.status !== "done") return;
    select(r.id);
    navigate("/");
  };
  return (
    <section className="block">
      <h2 className="block-title">Прошлые проверки</h2>
      {loading ? (
        <Skeleton lines={3} />
      ) : error ? (
        <ErrorNote error={error} />
      ) : !runs?.length ? (
        <Empty title="Проверок пока нет">Первая проверка появится здесь, как только вы загрузите выгрузку.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table runs">
            <thead>
              <tr>
                <th>Файл</th>
                <th>Период</th>
                <th>Когда</th>
                <th>Статус</th>
                <th className="r">Закупки</th>
                <th className="r">Переплата</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className={r.status === "done" ? "clickable" : ""} onClick={() => open(r)}>
                  <td className="ellipsis" title={r.filename}>
                    {r.status === "done" ? (
                      <a
                        href="/"
                        onClick={(e) => {
                          e.preventDefault();
                          open(r);
                        }}
                      >
                        {r.filename}
                      </a>
                    ) : (
                      r.filename
                    )}
                  </td>
                  <td className="num">{r.period_start ? `${date(r.period_start)} — ${date(r.period_end)}` : "—"}</td>
                  <td className="num muted">{dayTime(r.created_at)}</td>
                  <td className={r.status === "failed" ? "err" : ""}>{RUN_STATUS[r.status] ?? r.status}</td>
                  <td className="r num">{r.status === "done" ? int(r.spend) : "—"}</td>
                  <td className="r num over">{r.status === "done" ? int(r.overpayment) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const RUN_STATUS: Record<string, string> = {
  queued: "В очереди",
  importing: "Читаем файл",
  normalizing: "Сводим номенклатуру",
  analyzing: "Сравниваем цены",
  reporting: "Готовим отчёт",
  done: "Готово",
  failed: "Ошибка",
};

// Источник «Из 1С»: забрать строки по OData-подключению или отправить обработкой из самой 1С
function OneCSource({ onQueued }: { onQueued: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const conns = useQuery({ queryKey: ["connections"], queryFn: () => unwrap(api.GET("/api/integrations/connections")) });
  // период по умолчанию — последние 12 месяцев (ленивая инициализация: дата берётся один раз)
  const [start, setStart] = useState(() => isoDay(-365));
  const [end, setEnd] = useState(() => isoDay(0));
  const sync = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/integrations/connections/{connection_id}/sync", { params: { path: { connection_id: id } }, body: { start, end } })),
    onSuccess: () => {
      toast({ text: "Забираем строки из 1С — проверка появится в списке ниже" });
      window.setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["runs"] });
        qc.invalidateQueries({ queryKey: ["connections"] });
      }, 3000);
      onQueued();
    },
    onError: (e) => toast({ kind: "error", text: e.message }),
  });
  if (conns.isPending) return <Skeleton lines={3} />;
  if (conns.isError) return <ErrorNote error={conns.error} />;
  return (
    <div className="file-step">
      {conns.data.length > 0 ? (
        <div className="panel">
          <div className="actions">
            <label className="field">
              <span className="field-label">С</span>
              <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">По</span>
              <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
          {conns.data.map((c) => (
            <div key={c.id} className="filecard">
              <Icon name="sheet" size={22} />
              <div className="filecard-main">
                <span className="filecard-name">{c.name}</span>
                <span className="muted small">
                  {c.base_url} · {c.last_sync_at ? `последний раз ${dayTime(c.last_sync_at)}` : "ещё не забирали"}
                  {c.last_status === "error" && <span className="err"> · {c.last_error}</span>}
                </span>
              </div>
              <button type="button" className="btn btn-primary" disabled={sync.isPending} onClick={() => sync.mutate(c.id)}>
                Забрать и проверить
              </button>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="1С ещё не подключена"
          action={
            <Link className="btn btn-primary" to="/settings#onec">
              Подключить 1С
            </Link>
          }
        >
          Два способа: обработка в самой 1С отправляет строки по токену — публиковать базу не нужно; или «Второй ключ» сам
          забирает документы по OData, если база опубликована.
        </Empty>
      )}
    </div>
  );
}

const isoDay = (offsetDays: number) => new Date(Date.now() + offsetDays * 86_400_000).toISOString().slice(0, 10);
