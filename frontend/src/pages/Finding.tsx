import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, unwrap, type FindingDetail, type PriceHistory } from "../api/client";
import { ErrorNote, Loading } from "../components/State";
import { STATUS_LABEL, UNIT, date, money, num } from "../format";
import { canAnswer, canDecide, useRole } from "../session";

export function FindingPage() {
  const { id = "" } = useParams();
  const role = useRole();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["finding", id],
    queryFn: () => unwrap(api.GET("/api/findings/{finding_id}", { params: { path: { finding_id: id } } })),
  });
  const history = useQuery({
    queryKey: ["history", q.data?.run_id, q.data?.sku],
    enabled: !!q.data?.sku,
    queryFn: () =>
      unwrap(api.GET("/api/audits/{run_id}/price-history", {
        params: { path: { run_id: q.data!.run_id }, query: { sku: q.data!.sku! } },
      })),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["finding", id] });
    qc.invalidateQueries({ queryKey: ["findings"] });
    qc.invalidateQueries({ queryKey: ["summary"] });
  };

  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorNote error={q.error} />;
  const f = q.data;
  const unit = UNIT[f.lines[0]?.unit_base ?? ""] ?? "";

  return (
    <section>
      <Link to="/findings">← Все вопросы</Link>
      <h2>{f.title}</h2>
      <p className="lead">{f.message}</p>
      <div className="facts">
        <span><span className="label">Оценка переплаты</span><strong>{money(f.overpayment)}</strong></span>
        {f.reference && <span><span className="label">Эталон цены</span><strong>{num(f.reference)} сом/{unit}</strong></span>}
        <span><span className="label">Статус</span><strong className={`status s-${f.status}`}>{STATUS_LABEL[f.status]}</strong></span>
      </div>

      {history.data && <PriceChart h={history.data} />}

      <h3>Строки учёта</h3>
      <div className="scroll">
        <table className="table">
          <thead>
            <tr><th>Дата</th><th>Документ</th><th>Поставщик</th><th>Номенклатура в учёте</th>
              <th className="num">Кол-во</th><th className="num">Цена</th><th className="num">Переплата</th></tr>
          </thead>
          <tbody>
            {f.lines.map((l) => (
              <tr key={l.id}>
                <td>{date(l.date)}</td>
                <td title={l.doc_no ?? ""}>стр. {l.source_row}</td>
                <td>{l.supplier}</td>
                <td>{l.item_raw}</td>
                <td className="num">{num(l.qty)} {l.unit_raw}</td>
                <td className="num">{num(l.price)}</td>
                <td className="num">{money(l.finding_overpayment)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Keys f={f} canAnswer={canAnswer(role)} canDecide={canDecide(role)} onChange={refresh} />
    </section>
  );
}

// Два ключа: снабжение отвечает (ключ 1), собственник решает (ключ 2); решение можно отменить
function Keys({ f, canAnswer, canDecide, onChange }: {
  f: FindingDetail; canAnswer: boolean; canDecide: boolean; onChange: () => void;
}) {
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const path = { params: { path: { finding_id: f.id } } };
  const answer = useMutation({
    mutationFn: () => unwrap(api.POST("/api/findings/{finding_id}/answer", { ...path, body: { answer: text } })),
    onSuccess: () => { setText(""); onChange(); },
  });
  const decide = useMutation({
    mutationFn: (status: "confirmed" | "justified" | "data_error") =>
      unwrap(api.POST("/api/findings/{finding_id}/decision", { ...path, body: { status, note: note || null } })),
    onSuccess: onChange,
  });
  const reopen = useMutation({
    mutationFn: () => unwrap(api.POST("/api/findings/{finding_id}/reopen", path)),
    onSuccess: onChange,
  });
  const decided = ["confirmed", "justified", "data_error"].includes(f.status);

  return (
    <div className="keys">
      <div className="key">
        <h3>Ключ 1 · объяснение снабжения</h3>
        {f.answer ? (
          <blockquote>{f.answer}<footer className="muted">{f.answered_by?.full_name} · {date(f.answered_at)}</footer></blockquote>
        ) : (
          <p className="muted">Ответа пока нет.</p>
        )}
        {canAnswer && !decided && (
          <>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
                      placeholder="Почему цена выше: срочность, доставка, качество, договорённость…" />
            <button disabled={text.trim().length < 3 || answer.isPending} onClick={() => answer.mutate()}>
              {f.answer ? "Дополнить ответ" : "Ответить"}
            </button>
            {answer.error && <ErrorNote error={answer.error} />}
          </>
        )}
      </div>
      <div className="key">
        <h3>Ключ 2 · решение собственника</h3>
        {decided ? (
          <>
            <p><strong>{STATUS_LABEL[f.status]}</strong>{f.decision_note && ` — ${f.decision_note}`}</p>
            <p className="muted">{f.decided_by?.full_name} · {date(f.decided_at)}</p>
            {canDecide && <button className="link" onClick={() => reopen.mutate()}>Вернуть в работу</button>}
          </>
        ) : canDecide ? (
          <>
            <input placeholder="Комментарий к решению (необязательно)" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="actions">
              <button className="primary" onClick={() => decide.mutate("confirmed")}>Переплата подтверждена</button>
              <button onClick={() => decide.mutate("justified")}>Обосновано</button>
              <button onClick={() => decide.mutate("data_error")}>Ошибка учёта</button>
            </div>
            {decide.error && <ErrorNote error={decide.error} />}
          </>
        ) : (
          <p className="muted">Решение принимает собственник или финансовый директор.</p>
        )}
      </div>
    </div>
  );
}

// Минимальный график: покупки по датам (отмеченные — с переплатой) и рыночные точки
function PriceChart({ h }: { h: PriceHistory }) {
  const pts = [...h.purchases, ...h.market];
  if (pts.length < 2) return null;
  const xs = pts.map((p) => new Date(p.date).getTime());
  const ys = pts.map((p) => Number(p.price));
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys) * 0.95, Math.max(...ys) * 1.05];
  const W = 640, H = 200;
  const x = (d: string) => ((new Date(d).getTime() - x0) / (x1 - x0 || 1)) * (W - 20) + 10;
  const y = (v: string) => H - 10 - ((Number(v) - y0) / (y1 - y0 || 1)) * (H - 20);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Цены: ${h.label}`}>
        {h.market.map((p, i) => <rect key={`m${i}`} x={x(p.date) - 2} y={y(p.price) - 2} width={4} height={4} className="pt-market" />)}
        {h.purchases.map((p, i) => <circle key={`p${i}`} cx={x(p.date)} cy={y(p.price)} r={p.flagged ? 4.5 : 3}
                                         className={p.flagged ? "pt-flag" : "pt-buy"}><title>{`${date(p.date)} · ${p.supplier} · ${num(p.price)}`}</title></circle>)}
      </svg>
      <figcaption className="muted">
        {h.label}, сом за {UNIT[h.unit ?? ""] ?? h.unit}: <span className="lg-buy">●</span> покупки,{" "}
        <span className="lg-flag">●</span> с переплатой, <span className="lg-market">■</span> рынок
      </figcaption>
    </figure>
  );
}
