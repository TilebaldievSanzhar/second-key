// «Связи»: кто у кого покупает и где сошлись следы. Связка — вопрос, а не вывод; денег она не добавляет.
// Только собственнику и финансам: следы о поставщиках и сотрудниках (docs/adr/0006-supplier-links.md).
import { Link, Navigate } from "react-router";
import type { LinkItem, LinksOverview } from "../api/client";
import { Icon } from "../components/Icon";
import { KeyStatus } from "../components/KeyPair";
import { LinkMatrix } from "../components/LinkMatrix";
import { RunPicker } from "../components/RunPicker";
import { Empty, ErrorNote, Skeleton } from "../components/State";
import { TraceGroups, TraceList } from "../components/Traces";
import { date, moneyShort, share } from "../format";
import { GROUPS, TRACE_TEXT, traceSummaries } from "../questions";
import { canSeeLinks, useCurrentRun, useLinks, useRole } from "../session";
import { MobileHead } from "./Layout";

export function LinksPage() {
  const role = useRole();
  const { run, loading } = useCurrentRun();
  const q = useLinks(run?.id, canSeeLinks(role));
  if (!canSeeLinks(role)) return <Navigate to="/" replace />;

  return (
    <section className="page links-page">
      <MobileHead>
        <span className="mhead-title">Связи</span>
        <RunPicker />
      </MobileHead>
      <header className="page-head">
        <div>
          <h1 className="page-title">Связи</h1>
          {run && <p className="page-meta">Снабженцы и поставщики · проверка от {date(run.created_at)}</p>}
        </div>
        <div className="page-actions">
          <RunPicker variant="boxed" text={(r) => `Проверка от ${date(r.created_at)}`} />
        </div>
      </header>
      {loading || (run && q.isPending) ? (
        <Skeleton variant="list" lines={5} />
      ) : !run ? (
        <Empty title="Проверок ещё не было" action={<Link to="/uploads" className="btn btn-primary">Загрузить выгрузку</Link>} />
      ) : q.isError ? (
        <ErrorNote error={q.error} retry={() => q.refetch()} />
      ) : (
        <Content data={q.data!} runId={run.id} />
      )}
    </section>
  );
}

function Content({ data, runId }: { data: LinksOverview; runId: string }) {
  const waiting = data.links.filter((l) => l.status === "open" || l.status === "answered");
  const full = data.links.filter((l) => l.groups.length === 3).length;
  const staff = data.links.filter((l) => l.traces.some((t) => t.code === "L-12")).length;
  const weak = data.suppliers.filter((s) => !s.linked && s.traces.length);
  const noOwner = data.buyers.length === 1 && data.buyers[0]!.name === null;

  return (
    <>
      <p className="note note-accent">
        <Icon name="link" size={18} strokeWidth={2} />
        <span>
          Связка — вопрос, а не вывод: у пары «снабженец — поставщик» сошлись независимые следы. Снабженец видит только
          вопрос о покупках; денег связка не добавляет — переплата уже в ценовых вопросах.
        </span>
      </p>

      <div className="stat-row">
        <div className="stat">
          <span className="hero-label">Связок ждут пояснения</span>
          <span className="stat-num">{waiting.length}</span>
          <span className="muted small">из {data.links.length} найденных</span>
        </div>
        <div className="stat">
          <span className="hero-label">Сошлись все три группы</span>
          <span className="stat-num">{full}</span>
          <span className="muted small">цена, поведение и сам поставщик</span>
        </div>
        <div className="stat">
          <span className="hero-label">Поставщик — ваш сотрудник</span>
          <span className="stat-num">{staff}</span>
          <span className="muted small">совпал ИНН · проверено {data.suppliers_total} поставщиков</span>
        </div>
      </div>

      <section className="block">
        <div className="block-head">
          <h2>Связки</h2>
          <span className="muted small">крупные сверху</span>
        </div>
        {data.links.length ? (
          <div className="link-list">
            {data.links.map((l) => (
              <LinkCard key={l.finding_id} l={l} runId={runId} />
            ))}
          </div>
        ) : (
          <Empty title="Связок не нашлось">
            Проверили {data.suppliers_total} поставщиков: премию через одного снабженца, эксклюзивность, приход вместе с
            новым снабженцем, вытеснение, номера счетов подряд, возраст по ИНН, общие реквизиты и совпадение с сотрудниками.
            Ни у одной пары следы не сошлись.
          </Empty>
        )}
      </section>

      <section className="block">
        <div className="block-head">
          <h2>Кто у кого покупает</h2>
          <span className="muted small desktop-only">наведите на клетку — подробности; нажмите — вопрос или карточка поставщика</span>
        </div>
        {noOwner ? (
          <p className="note note-over">
            <Icon name="alert" size={18} strokeWidth={2} />
            <span>
              В выгрузке нет ответственных: матрица и следы пары недоступны. Добавьте в отчёт 1С колонку «Ответственный»
              (или «Менеджер») — следы о самих поставщиках работают и без неё.
            </span>
          </p>
        ) : (
          <LinkMatrix data={data} runId={runId} />
        )}
      </section>

      {weak.length > 0 && (
        <section className="block">
          <details className="weak">
            <summary>
              <h2>Следы без вопроса</h2>
              <span className="muted small">{weak.length} · сошлись не в двух группах — для сведения</span>
            </summary>
            <ul className="weak-list">
              {weak.map((s) => (
                <li key={s.id}>
                  <Link to={`/suppliers/${s.id}?run=${runId}`} className="weak-name">
                    {s.name}
                  </Link>
                  <span className="weak-traces">
                    {s.traces.map((t, i) => (
                      <span key={t.code + i} title={t.text}>
                        {TRACE_TEXT[t.code] ?? t.code}
                        {t.responsible ? ` · ${t.responsible}` : ""}
                      </span>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}
    </>
  );
}

function LinkCard({ l, runId }: { l: LinkItem; runId: string }) {
  return (
    <article className={`link-card${l.severity === "high" ? " link-card-high" : ""}`}>
      <header className="link-head">
        <h3 className="link-pair">
          <span>{l.responsible ?? "Разные сотрудники"}</span>
          <Icon name="link" size={16} strokeWidth={2.2} />
          {l.supplier_id ? (
            <Link to={`/suppliers/${l.supplier_id}?run=${runId}`}>{l.supplier}</Link>
          ) : (
            <span>{l.supplier}</span>
          )}
        </h3>
        <KeyStatus status={l.status} code="LINK" />
      </header>
      <TraceGroups groups={l.groups} />
      <ul className="link-sum">
        {GROUPS.filter((g) => l.traces.some((t) => t.group === g.key)).map((g) => (
          <li key={g.key}>
            <span className="link-sum-group">{g.label}</span>
            <span>{traceSummaries(l.traces.filter((t) => t.group === g.key)).join(" · ")}</span>
          </li>
        ))}
      </ul>
      <details className="link-more">
        <summary>Что увидели</summary>
        <TraceList traces={l.traces} compact />
      </details>
      <footer className="link-foot">
        <span className="muted small">
          Оборот пары {moneyShort(l.pair_spend)} · {share(l.pair_share)} оборота поставщика
          {Number(l.overpayment) > 0 ? ` · по ценовым вопросам этих покупок — ${moneyShort(l.overpayment)}, уже в итоге` : ""}
        </span>
        <span className="actions">
          <Link className="btn btn-secondary btn-sm" to={`/findings/${l.finding_id}`}>
            Открыть вопрос
          </Link>
          {l.supplier_id && (
            <Link className="btn btn-link" to={`/suppliers/${l.supplier_id}?run=${runId}`}>
              Карточка поставщика
            </Link>
          )}
        </span>
      </footer>
    </article>
  );
}
