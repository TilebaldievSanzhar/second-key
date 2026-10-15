import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { api, unwrap, type Run, type Upload } from "../api/client";
import { Empty, ErrorNote, Loading } from "../components/State";
import { RUN_STAGE, date, money } from "../format";
import { canUpload, useRole } from "../session";

const FIELD_LABEL: Record<string, string> = {
  date: "Дата", supplier: "Поставщик", item: "Номенклатура", qty: "Количество", price: "Цена", amount: "Сумма",
  unit: "Ед. изм.", doc: "Документ", inn: "ИНН", vat: "НДС", object: "Объект", responsible: "Ответственный",
};

export function UploadsPage() {
  const role = useRole();
  const qc = useQueryClient();
  const [upload, setUpload] = useState<Upload | null>(null);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/audits")) });
  const refreshRuns = useCallback(() => qc.invalidateQueries({ queryKey: ["runs"] }), [qc]);

  const send = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      // multipart: тело передаём как есть, тип границы проставит браузер
      return unwrap(api.POST("/api/uploads", { body: body as never, bodySerializer: (b) => b as unknown as FormData }));
    },
    onSuccess: setUpload,
  });
  const start = useMutation({
    mutationFn: (uploadId: string) => unwrap(api.POST("/api/audits", { body: { upload_id: uploadId } })),
    onSuccess: (run) => {
      setUpload(null);
      setActiveRun(run.id);
      refreshRuns();
    },
  });

  return (
    <section>
      <h2>Загрузки</h2>
      {canUpload(role) && (
        <div className="panel">
          <p>Выгрузка из 1С: «Поступление товаров и услуг» по строкам за 6–12 месяцев, Excel или CSV.</p>
          <input type="file" accept=".xlsx,.xlsm,.csv" disabled={send.isPending}
                 onChange={(e) => e.target.files?.[0] && send.mutate(e.target.files[0])} />
          {send.isPending && <Loading label="Читаем файл…" />}
          {send.error && <ErrorNote error={send.error} />}
          {upload && <Preview upload={upload} onStart={() => start.mutate(upload.id)} starting={start.isPending} />}
          {start.error && <ErrorNote error={start.error} />}
        </div>
      )}
      {activeRun && <Progress runId={activeRun} onDone={refreshRuns} />}
      <h3>Проверки</h3>
      {runs.isPending ? <Loading /> : runs.isError ? <ErrorNote error={runs.error} /> : runs.data.items.length === 0 ? (
        <Empty title="Проверок пока нет" />
      ) : (
        <table className="table">
          <thead>
            <tr><th>Файл</th><th>Период</th><th>Статус</th><th className="num">Закупки</th><th className="num">Переплата</th></tr>
          </thead>
          <tbody>
            {runs.data.items.map((r: Run) => (
              <tr key={r.id}>
                <td>{r.filename}</td>
                <td>{date(r.period_start)} — {date(r.period_end)}</td>
                <td>{RUN_STAGE[r.status]}{r.error && <span className="error"> · {r.error}</span>}</td>
                <td className="num">{money(r.spend)}</td>
                <td className="num">{r.status === "done" ? <Link to={`/findings?run=${r.id}`}>{money(r.overpayment)}</Link> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Preview({ upload, onStart, starting }: { upload: Upload; onStart: () => void; starting: boolean }) {
  const p = upload.preview;
  const byIndex = Object.fromEntries(Object.entries(p.mapping).map(([field, i]) => [i, field]));
  return (
    <div className="preview">
      <p>
        <strong>{upload.filename}</strong> — шапка в строке {p.header_row || "не найдена"}.
        {p.missing.length > 0 && <span className="error"> Не нашли колонки: {p.missing.map((f) => FIELD_LABEL[f] ?? f).join(", ")}.</span>}
      </p>
      <div className="scroll">
        <table className="table compact">
          <thead>
            <tr>{p.columns.map((c, i) => <th key={i}>{c}<br /><small className="muted">{FIELD_LABEL[byIndex[i] ?? ""] ?? "—"}</small></th>)}</tr>
          </thead>
          <tbody>
            {p.sample.slice(0, 5).map((row, i) => <tr key={i}>{row.map((c, j) => <td key={j}>{c}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      <button className="primary" disabled={starting || p.missing.length > 0} onClick={onStart}>
        {starting ? "Ставим в очередь…" : "Колонки верны — проверить закупки"}
      </button>
    </div>
  );
}

// Прогресс по SSE: первое событие — текущее состояние, дальше — смена стадий до done/failed
function Progress({ runId, onDone }: { runId: string; onDone: () => void }) {
  const [state, setState] = useState<{ status: string; progress: number; error?: string | null }>({
    status: "queued", progress: 0,
  });
  useEffect(() => {
    const es = new EventSource(`/api/audits/${runId}/events`, { withCredentials: true });
    es.addEventListener("progress", (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      setState(data);
      if (data.status === "done" || data.status === "failed") {
        es.close();
        onDone();
      }
    });
    return () => es.close();
  }, [runId, onDone]);
  return (
    <div className="panel" aria-live="polite">
      <p>{RUN_STAGE[state.status] ?? state.status} · {state.progress}%</p>
      <progress max={100} value={state.progress} />
      {state.error && <p className="error">{state.error}</p>}
      {state.status === "done" && <Link to={`/findings?run=${runId}`}>Смотреть вопросы →</Link>}
    </div>
  );
}
