import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, unwrap } from "../api/client";
import { Empty, ErrorNote, Loading } from "../components/State";
import { date, money, pct } from "../format";

// Сводка собственника: сколько проверено, сколько переплачено, что ждёт решения
export function OverviewPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/audits")) });
  const last = runs.data?.items.find((r) => r.status === "done");
  const summary = useQuery({
    queryKey: ["summary", last?.id],
    enabled: !!last,
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}/summary", { params: { path: { run_id: last!.id } } })),
  });

  if (runs.isPending) return <Loading />;
  if (runs.isError) return <ErrorNote error={runs.error} />;
  if (!last)
    return (
      <Empty title="Проверок ещё не было">
        <p>Загрузите выгрузку закупок из 1С за 6–12 месяцев — первая проверка займёт пару минут.</p>
        <Link className="button primary" to="/uploads">Загрузить выгрузку</Link>
      </Empty>
    );
  if (summary.isPending) return <Loading />;
  if (summary.isError) return <ErrorNote error={summary.error} />;
  const s = summary.data;
  const waiting = s.findings.open + s.findings.answered;

  return (
    <section>
      <p className="muted">
        Закупки {date(s.run.period_start)} — {date(s.run.period_end)} · {s.run.lines_total} строк · {s.run.filename}
      </p>
      <div className="hero">
        <div>
          <span className="label">Возможная переплата</span>
          <strong className="big">{money(s.run.overpayment)}</strong>
          <span className="muted">{pct(s.run.overpayment, s.run.spend)} от {money(s.run.spend)} закупок</span>
        </div>
        <div>
          <span className="label">Подтверждено вами</span>
          <strong className="mid">{money(s.confirmed_overpayment)}</strong>
        </div>
        <div>
          <span className="label">Ждут решения</span>
          <strong className="mid">{waiting}</strong>
          <Link to="/findings">Открыть вопросы →</Link>
        </div>
      </div>
      <div className="grid2">
        <Breakdown title="По поставщикам" rows={s.by_supplier} />
        <Breakdown title="По ответственным" rows={s.by_responsible} />
        <Breakdown title="По материалам" rows={s.by_material} />
        <Breakdown title="По правилам" rows={s.by_rule} />
      </div>
      <p>
        <a href={`/api/audits/${s.run.id}/report`} target="_blank" rel="noreferrer">Отчёт для печати</a>
      </p>
    </section>
  );
}

function Breakdown({ title, rows }: { title: string; rows: { key: string; label: string; overpayment: string }[] }) {
  return (
    <div>
      <h3>{title}</h3>
      <table className="table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td className="num">{money(r.overpayment)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
