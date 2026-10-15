import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, unwrap, type FindingStatus } from "../api/client";
import { Empty, ErrorNote, Loading } from "../components/State";
import { STATUS_LABEL, money } from "../format";

const FILTERS: { label: string; status: FindingStatus[] }[] = [
  { label: "Ждут решения", status: ["open", "answered"] },
  { label: "Решённые", status: ["confirmed", "justified", "data_error"] },
  { label: "Все", status: [] },
];

// Входящие вопросы: крупнейшие деньги сверху, язык — вопрос, а не обвинение
export function FindingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = Number(params.get("tab") ?? 0);
  const filter = FILTERS[tab] ?? FILTERS[0]!;
  // по умолчанию — последний завершённый аудит: повторные выгрузки за тот же период не дублируют вопросы
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/audits")) });
  const runId = params.get("run") ?? runs.data?.items.find((r) => r.status === "done")?.id;
  const q = useQuery({
    queryKey: ["findings", runId, filter.status],
    enabled: runs.isFetched,
    queryFn: () =>
      unwrap(api.GET("/api/findings", { params: { query: { run_id: runId, status: filter.status.length ? filter.status : undefined, limit: 200 } } })),
  });

  return (
    <section>
      <h2>Вопросы по закупкам</h2>
      <div className="tabs" role="tablist">
        {FILTERS.map((f, i) => (
          <button key={f.label} role="tab" aria-selected={i === tab} className={i === tab ? "active" : ""}
                  onClick={() => setParams((p) => { p.set("tab", String(i)); return p; })}>
            {f.label}
          </button>
        ))}
      </div>
      {q.isPending ? <Loading /> : q.isError ? <ErrorNote error={q.error} /> : q.data.items.length === 0 ? (
        <Empty title={tab === 0 ? "Все вопросы разобраны" : "Здесь пока пусто"} />
      ) : (
        <ul className="list">
          {q.data.items.map((f) => (
            <li key={f.id}>
              <Link to={`/findings/${f.id}`} className="row">
                <span className="row-main">
                  <span className="row-title">{f.title}</span>
                  <span className="muted">
                    {f.sku_label ?? "—"} · {f.supplier ?? "несколько поставщиков"} · {f.responsible ?? "ответственный не указан"}
                  </span>
                </span>
                <span className="row-side">
                  <strong className="num">{Number(f.overpayment) > 0 ? money(f.overpayment) : "сигнал"}</strong>
                  <span className={`status s-${f.status}`}>{STATUS_LABEL[f.status]}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
