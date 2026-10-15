// Сводка собственника: одна главная сумма, что ждёт решения, крупнейшие вопросы, где переплата.
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import type { RunSummary } from "../api/client";
import { BreakdownList, CUT_TITLE, type Cut } from "../components/Breakdown";
import { FindingRow } from "../components/FindingRow";
import { Icon } from "../components/Icon";
import { KeyPair } from "../components/KeyPair";
import { Money } from "../components/Money";
import { RunPicker } from "../components/RunPicker";
import { StageProgress } from "../components/StageProgress";
import { Empty, ErrorNote, Skeleton } from "../components/State";
import { LINES, QUESTIONS, count, date, dayMonth, int, moneyShort, pct } from "../format";
import { canUpload, useCurrentRun, useRole, useRunFindings, useSummary, waitingQueue } from "../session";
import { MobileHead } from "./Layout";

const CUTS: Cut[] = ["supplier", "responsible", "material", "rule"];

export function OverviewPage() {
  const { run, runs, loading } = useCurrentRun();
  const role = useRole();
  const summary = useSummary(run?.id);
  const findings = useRunFindings(run?.id);
  const navigate = useNavigate();
  const running = runs.find((r) => r.status !== "done" && r.status !== "failed");

  if (loading) return <Skeleton lines={6} />;
  if (!run)
    return (
      <section className="page">
        <MobileHead>
          <span className="mhead-title">Сводка</span>
        </MobileHead>
        {running ? (
          <div className="panel narrow">
            <StageProgress status={running.status} progress={running.progress} />
          </div>
        ) : (
          <Empty
            title="Проверок ещё не было"
            action={
              canUpload(role) ? (
                <Link className="btn btn-primary" to="/uploads">
                  Загрузить выгрузку
                </Link>
              ) : (
                <a className="btn btn-secondary" href={`https://wa.me/?text=${encodeURIComponent(ASK_ACCOUNTANT)}`} target="_blank" rel="noreferrer">
                  Попросить бухгалтера в WhatsApp
                </a>
              )
            }
          >
            Загрузите выгрузку закупок из 1С за 6–12 месяцев — первая проверка займёт пару минут.
          </Empty>
        )}
      </section>
    );
  if (summary.isPending) return <Skeleton lines={6} />;
  if (summary.isError) return <ErrorNote error={summary.error} retry={() => summary.refetch()} />;

  const s = summary.data;
  const queue = waitingQueue(findings.data);
  const top = (findings.data ?? []).filter((f) => f.status === "open" || f.status === "answered").slice(0, 5);
  const start = () => (queue[0] ? navigate(`/findings/${queue[0].id}`) : navigate("/findings"));

  return (
    <section className="page overview">
      <MobileHead>
        <RunPicker />
      </MobileHead>
      <header className="page-head">
        <div>
          <h1 className="page-title">Сводка</h1>
          <p className="page-meta">{runMeta(s)}</p>
        </div>
        <div className="page-actions">
          <a className="btn btn-link" href={`/api/audits/${s.run.id}/report`} target="_blank" rel="noreferrer">
            <Icon name="printer" size={16} strokeWidth={2} />
            Отчёт для печати
          </a>
          <RunPicker variant="boxed" text={(r) => `Проверка от ${date(r.created_at)}`} />
        </div>
      </header>
      <p className="page-meta mobile-only">{runMeta(s)}</p>
      {running && running.id !== s.run.id && (
        <p className="note note-accent">
          Идёт новая проверка «{running.filename}» — {running.progress}%. Сводка обновится, когда она закончится.
        </p>
      )}

      <div className="hero">
        <div className="hero-main">
          <span className="hero-label">Возможная переплата</span>
          <Money value={s.run.overpayment} tone="over" size="hero" />
          <span className="muted">
            {Number(s.run.overpayment) > 0
              ? `${pct(s.run.overpayment, s.run.spend)} от ${moneyShort(s.run.spend)} закупок`
              : `Переплат не нашли: проверено ${count(s.run.lines_total, LINES)} на ${moneyShort(s.run.spend)}`}
          </span>
          <button type="button" className="btn btn-primary btn-xl hero-cta" onClick={start} disabled={!queue.length}>
            Разобрать вопросы<span className="btn-count">· {queue.length}</span>
          </button>
        </div>
        <div className="hero-side">
          <span className="hero-label">Подтверждено вами</span>
          {decidedCount(s) ? (
            <>
              <Money value={s.confirmed_overpayment} tone="confirmed" size="l" unit="" />
              <span className="muted small">
                сом · {count(s.findings.confirmed, QUESTIONS)}
                <span className="desktop-only">
                  {" "}
                  · обосновано {s.findings.justified} · ошибка учёта {s.findings.data_error}
                </span>
              </span>
            </>
          ) : (
            <>
              <span className="hero-num muted">0</span>
              <span className="muted small">Решений пока нет — начните с крупнейших вопросов</span>
            </>
          )}
        </div>
        <div className="hero-side">
          <span className="hero-label">Ждут решения</span>
          <span className="hero-num">{int(s.findings.open + s.findings.answered)}</span>
          <span className="keyline">
            <KeyPair status="answered" size="s" />
            {s.findings.answered} ждут вашего ключа
          </span>
          <span className="keyline muted">
            <KeyPair status="open" size="s" />
            {s.findings.open} ждут ответа снабжения
          </span>
        </div>
      </div>

      <section className="block">
        <div className="block-head">
          <h2>Крупнейшие вопросы</h2>
          <Link to="/findings">Все вопросы · {s.findings.open + s.findings.answered}</Link>
        </div>
        {top.length ? (
          <ul className="flist flist-top">
            {top.map((f, i) => (
              <FindingRow key={f.id} f={f} showRule className={i >= 3 ? "desktop-only-row" : undefined} />
            ))}
          </ul>
        ) : (
          <Empty title="Все вопросы разобраны">
            Подтверждено {moneyShort(s.confirmed_overpayment)}, обосновано {count(s.findings.justified, QUESTIONS)}.
          </Empty>
        )}
      </section>

      <Cuts s={s} />
    </section>
  );
}

function Cuts({ s }: { s: RunSummary }) {
  const [tab, setTab] = useState<Cut>("supplier");
  const rows = { supplier: s.by_supplier, responsible: s.by_responsible, material: s.by_material, rule: s.by_rule };
  return (
    <section className="block">
      <h2 className="block-title">Где переплата</h2>
      <div className="seg mobile-only" role="tablist">
        {CUTS.map((c) => (
          <button key={c} type="button" role="tab" aria-selected={tab === c} className={tab === c ? "on" : ""} onClick={() => setTab(c)}>
            {CUT_TITLE[c]}
          </button>
        ))}
      </div>
      <div className="mobile-only cut-card">
        <BreakdownList cut={tab} rows={rows[tab]} />
      </div>
      <div className="cuts desktop-only">
        {CUTS.map((c) => (
          <div key={c} className="cut-card">
            <h3>{CUT_TITLE[c]}</h3>
            <BreakdownList cut={c} rows={rows[c]} limit={4} />
          </div>
        ))}
      </div>
    </section>
  );
}

const decidedCount = (s: RunSummary) => s.findings.confirmed + s.findings.justified + s.findings.data_error;

const runMeta = (s: RunSummary) =>
  `Закупки ${date(s.run.period_start)} — ${date(s.run.period_end)} · ${count(s.run.lines_total, LINES)} · выгрузка от ${dayMonth(s.run.created_at)}`;

const ASK_ACCOUNTANT =
  "Добрый день! Выгрузите, пожалуйста, из 1С отчёт «Поступление товаров и услуг» по строкам за последние 12 месяцев в Excel и загрузите его во «Второй ключ».";
