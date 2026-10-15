// Карточка вопроса: суть, факты, график цен, строки учёта и два ключа.
// Режим разбора: решение → следующий ожидающий вопрос, тост «Отменить» 6 секунд, потом — «Вернуть в работу».
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, unwrap, type Finding, type FindingDetail, type FindingLine } from "../api/client";
import { Icon } from "../components/Icon";
import { KeySeg, KeyStatus } from "../components/KeyPair";
import { Money } from "../components/Money";
import { PriceChart } from "../components/PriceChart";
import { ErrorNote, Skeleton } from "../components/State";
import { useToast } from "../components/Toast";
import {
  LINES,
  PER_UNIT,
  PURCHASES,
  count,
  date,
  dateTime,
  dayMonth,
  docNumber,
  int,
  price,
  qty,
  unit as unitText,
} from "../format";
import {
  DECISIONS,
  REASONS,
  RULE_TEXT,
  STATUS_TEXT,
  explanation,
  isDecided,
  isSignal,
  questionTitle,
  type Decision,
  type Reason,
} from "../questions";
import { canAnswer, canDecide, useRole, useRunFindings, waitingQueue } from "../session";

export function FindingPage() {
  const { id = "" } = useParams();
  const q = useQuery({
    queryKey: ["finding", id],
    queryFn: () => unwrap(api.GET("/api/findings/{finding_id}", { params: { path: { finding_id: id } } })),
  });
  if (q.isPending) return <Skeleton lines={10} />;
  if (q.isError)
    return (
      <section className="page">
        <BackLink />
        <ErrorNote error={q.error} retry={() => q.refetch()} />
      </section>
    );
  // key: состояние формы (комментарий, черновик ответа) не переезжает на следующий вопрос
  return <Card key={q.data.id} f={q.data} />;
}

function BackLink({ buyer = false }: { buyer?: boolean }) {
  return (
    <Link to="/findings" className="back">
      <Icon name="back" size={20} strokeWidth={2} />
      {buyer ? "Мои вопросы" : "Вопросы"}
    </Link>
  );
}

function Card({ f }: { f: FindingDetail }) {
  const role = useRole();
  const buyer = role === "buyer";
  const decider = canDecide(role);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const all = useRunFindings(f.run_id);
  const run = useQuery({
    queryKey: ["run", f.run_id],
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}", { params: { path: { run_id: f.run_id } } })),
  });
  const history = useQuery({
    queryKey: ["history", f.run_id, f.sku],
    enabled: !!f.sku,
    queryFn: () =>
      unwrap(api.GET("/api/audits/{run_id}/price-history", { params: { path: { run_id: f.run_id }, query: { sku: f.sku! } } })),
  });
  const [note, setNote] = useState("");

  // очередь разбора: собственнику — ждущие решения, снабжению — ждущие его ответа
  const items = useMemo(() => all.data ?? [], [all.data]);
  const queue = buyer ? items.filter((x) => x.status === "open") : waitingQueue(items);
  const inQueue = queue.findIndex((x) => x.id === f.id);
  const navList = inQueue >= 0 ? queue : items;
  const pos = navList.findIndex((x) => x.id === f.id);
  const go = (to: Finding | undefined) => to && navigate(`/findings/${to.id}`);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["findings", f.run_id] });
    qc.invalidateQueries({ queryKey: ["finding"] });
    qc.invalidateQueries({ queryKey: ["summary", f.run_id] });
  };

  const reopen = useMutation({
    mutationFn: () => unwrap(api.POST("/api/findings/{finding_id}/reopen", { params: { path: { finding_id: f.id } } })),
    onSuccess: () => {
      refresh();
      navigate(`/findings/${f.id}`);
    },
    onError: (e) => toast({ kind: "error", text: e.message }),
  });

  const decide = useMutation({
    mutationFn: (status: Decision) =>
      unwrap(
        api.POST("/api/findings/{finding_id}/decision", {
          params: { path: { finding_id: f.id } },
          body: { status, note: note.trim() || null },
        }),
      ),
    onSuccess: (_, status) => {
      const next = inQueue >= 0 ? (queue[inQueue + 1] ?? queue[inQueue - 1]) : undefined;
      const label = DECISIONS.find((d) => d.status === status)!.label;
      refresh();
      toast({
        text: inQueue >= 0 ? `Вопрос ${inQueue + 1} из ${queue.length}: «${label}» · сохранено` : `Решение сохранено: «${label}»`,
        action: { label: "Отменить", run: () => reopen.mutate() },
      });
      navigate(next ? `/findings/${next.id}` : "/findings");
    },
    onError: (e) =>
      toast({
        kind: "error",
        ms: 8000,
        text: e.message.startsWith("Нет связи") ? "Не удалось сохранить решение: нет связи. Решение не потеряно — нажмите ещё раз." : e.message,
      }),
  });

  const decided = isDecided(f.status);
  const canDecideNow = decider && !decided;

  // клавиатура: J/K — по списку, 1/2/3 — решения
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.key === "j") go(navList[pos + 1]);
      else if (e.key === "k") go(navList[pos - 1]);
      else if (canDecideNow && !decide.isPending) {
        const d = DECISIONS.find((x) => x.key === e.key);
        if (d) decide.mutate(d.status);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const u = f.lines[0]?.unit_base ?? history.data?.unit ?? null;
  const tolerance = Number((run.data?.params as Record<string, unknown> | undefined)?.market_tolerance ?? 0.1);
  const lineIds = useMemo(() => new Set(f.lines.map((l) => l.id)), [f.lines]);

  const chart =
    history.data && history.data.purchases.length + history.data.market.length >= 2 ? (
      <section className="block">
        <div className="block-head">
          <h2>Цена за {PER_UNIT[u ?? ""] ?? unitText(u)} · все покупки позиции</h2>
          <span className="muted small">{rangeText(history.data.purchases.map((p) => p.date))}</span>
        </div>
        <PriceChart
          history={history.data}
          supplier={f.supplier}
          lineIds={lineIds}
          reference={f.reference ? Number(f.reference) : null}
          tolerance={tolerance}
          height={typeof window !== "undefined" && window.innerWidth < 768 ? 210 : 280}
        />
        {history.data.market.length === 0 && <p className="muted small">Рыночных цен по позиции пока нет — сравниваем только с вашими поставщиками.</p>}
      </section>
    ) : null;

  return (
    <section className={`page card-page${canDecideNow ? " with-bar" : ""}`}>
      <div className="card-top">
        <BackLink buyer={buyer} />
        {pos >= 0 && (
          <div className="card-pos">
            <span className="mobile-only">
              {inQueue >= 0 ? (buyer ? `${inQueue + 1} из ${queue.length}` : `Разбор · осталось ${queue.length - inQueue}`) : `${pos + 1} из ${navList.length}`}
            </span>
            <span className="desktop-only num">
              {pos + 1} из {navList.length}
            </span>
            <button type="button" className="btn btn-icon desktop-only" onClick={() => go(navList[pos - 1])} disabled={pos <= 0} aria-label="Предыдущий вопрос (K)">
              <Icon name="up" size={16} strokeWidth={2} />
            </button>
            <button type="button" className="btn btn-icon desktop-only" onClick={() => go(navList[pos + 1])} disabled={pos >= navList.length - 1} aria-label="Следующий вопрос (J)">
              <Icon name="down" size={16} strokeWidth={2} />
            </button>
            <kbd className="desktop-only">K</kbd>
            <kbd className="desktop-only">J</kbd>
          </div>
        )}
      </div>
      {inQueue >= 0 && !buyer && (
        <div className="review-bar mobile-only" aria-hidden="true">
          <span style={{ width: `${(inQueue / Math.max(queue.length, 1)) * 100}%` }} />
        </div>
      )}

      <div className="card-grid">
        <div className="card-main">
          <header className="q-head">
            {buyer ? (
              <KeyStatus status={f.status} size="s" />
            ) : (
              <p className="q-rule">{RULE_TEXT[f.code] ?? f.title}</p>
            )}
            <h1 className="q-title">{questionTitle(f)}</h1>
            <p className="q-text">{explanation(f.message)}</p>
          </header>

          <Facts f={f} unit={u} buyer={buyer} />

          {buyer && (
            <p className="note note-accent">
              Система сравнивает цены автоматически. Объясните, почему цена такая, — решение примет собственник.
            </p>
          )}
          {buyer && canAnswer(role) && <AnswerBox f={f} queue={queue} />}

          {!buyer && chart}

          <section className="block keys-inline">
            <h2 className="block-title">Ключи</h2>
            <KeyOne f={f} />
            <KeyTwo f={f} decider={decider} onReopen={() => reopen.mutate()} reopening={reopen.isPending} compact />
          </section>

          {buyer && chart}

          <Lines lines={f.lines} unit={u} total={f.overpayment} file={run.data?.filename} signal={isSignal(f)} />
        </div>

        <aside className="card-aside">
          <KeyOne f={f} />
          {canDecideNow ? (
            <DecisionPanel note={note} setNote={setNote} onDecide={(d) => decide.mutate(d)} pending={decide.isPending} />
          ) : (
            <KeyTwo f={f} decider={decider} onReopen={() => reopen.mutate()} reopening={reopen.isPending} />
          )}
          <History f={f} />
        </aside>
      </div>

      {canDecideNow && (
        <div className="decision-bar">
          <input className="input" placeholder="Комментарий, необязательно" value={note} onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="btn btn-danger btn-lg" disabled={decide.isPending} onClick={() => decide.mutate("confirmed")}>
            Переплата подтверждена
          </button>
          <div className="decision-row">
            <button type="button" className="btn btn-outline btn-lg" disabled={decide.isPending} onClick={() => decide.mutate("justified")}>
              Обосновано
            </button>
            <button type="button" className="btn btn-secondary btn-lg" disabled={decide.isPending} onClick={() => decide.mutate("data_error")}>
              Ошибка учёта
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function Facts({ f, unit, buyer }: { f: FindingDetail; unit: string | null; buyer: boolean }) {
  const u = unitText(unit);
  const ev = (f.evidence ?? {}) as Record<string, unknown>;
  const cheaper = (ev.cheaper_suppliers as string[] | undefined)?.[0];
  const dates = f.lines.map((l) => l.date).sort();
  const volume = f.lines.reduce((a, l) => a + Number(l.qty_base ?? 0), 0);
  const ref =
    f.reference && f.reference_kind === "parallel"
      ? { label: "Эталон", value: `${int(f.reference)} сом/${u}`, sub: cheaper ?? "другой поставщик" }
      : f.reference && f.reference_kind === "market"
        ? { label: "Медиана рынка", value: `${int(f.reference)} сом/${u}`, sub: "госзакупки КР" }
        : f.reference
          ? { label: "Эталон", value: `${int(f.reference)} сом/${u}`, sub: "" }
          : null;
  const tone = f.status === "confirmed" ? "confirmed" : decidedMuted(f) ? "muted" : "over";
  return (
    <div className="facts">
      <div className="fact fact-money">
        <span className="fact-label">{buyer && f.reference_kind === "market" ? "Разница с рынком" : f.status === "confirmed" ? "Подтверждённая переплата" : "Возможная переплата"}</span>
        {isSignal(f) ? <span className="fact-signal">сигнал, без оценки в деньгах</span> : <Money value={f.overpayment} tone={tone} size="xl" />}
      </div>
      {buyer ? (
        volume > 0 && (
          <div className="fact">
            <span className="fact-label">Объём</span>
            <span className="fact-value">
              {qty(volume)} {u}
            </span>
            <span className="fact-sub">{count(f.lines.length, PURCHASES)}</span>
          </div>
        )
      ) : (
        ref && (
          <div className="fact">
            <span className="fact-label">{ref.label}</span>
            <span className="fact-value">{ref.value}</span>
            <span className="fact-sub">{ref.sub}</span>
          </div>
        )
      )}
      <div className="fact">
        <span className="fact-label">Покупки</span>
        <span className="fact-value">{count(f.lines.length, LINES)}</span>
        <span className="fact-sub">{dates.length ? `${dayMonth(dates[0])} — ${date(dates[dates.length - 1])}` : "—"}</span>
      </div>
      {!buyer && (
        <div className="fact desktop-only">
          <span className="fact-label">Ответственный</span>
          <span className="fact-value">{f.responsible ?? "не указан"}</span>
          <span className="fact-sub">{f.lines[0]?.object_name ?? ""}</span>
        </div>
      )}
    </div>
  );
}

const decidedMuted = (f: Finding) => f.status === "justified" || f.status === "data_error";

function KeyOne({ f }: { f: FindingDetail }) {
  return (
    <div className={`keycard${f.answer ? "" : " keycard-pending"}`}>
      <span className="keycard-label">
        <KeySeg state={f.answer ? "on" : "off"} />
        Ключ 1 · Объяснение снабжения
      </span>
      {f.answer ? (
        <>
          {f.answer_reasons && f.answer_reasons.length > 0 && (
            <div className="tags">
              {f.answer_reasons.map((r) => (
                <span key={r} className="tag">
                  {r}
                </span>
              ))}
            </div>
          )}
          <p className="keycard-text">{f.answer}</p>
          <span className="keycard-meta">
            {f.answered_by?.full_name ?? f.responsible} · {dateTime(f.answered_at)}
          </span>
        </>
      ) : (
        <span className="keycard-strong">Ждём ответа{f.responsible ? ` · ${f.responsible}` : ""}</span>
      )}
    </div>
  );
}

function KeyTwo({
  f,
  decider,
  onReopen,
  reopening,
  compact = false,
}: {
  f: FindingDetail;
  decider: boolean;
  onReopen: () => void;
  reopening: boolean;
  compact?: boolean;
}) {
  const decided = isDecided(f.status);
  return (
    <div className={`keycard${decided ? ` keycard-${f.status}` : " keycard-pending"}`}>
      <span className="keycard-label">
        <KeySeg state={decided ? f.status : "off"} />
        Ключ 2 · Решение
      </span>
      {decided ? (
        <>
          <span className={`keycard-strong st-${f.status}`}>{STATUS_TEXT[f.status]}</span>
          {f.decision_note && <p className="keycard-text">{f.decision_note}</p>}
          <span className="keycard-meta">
            {f.decided_by?.full_name} · {dateTime(f.decided_at)}
          </span>
          {decider && (
            <button type="button" className="btn btn-secondary keycard-btn" onClick={onReopen} disabled={reopening}>
              Вернуть в работу
            </button>
          )}
        </>
      ) : (
        <span className="keycard-strong">
          {decider ? (compact ? "Ваш ключ. Решения внизу экрана." : "Ваш ключ") : "Решение принимает собственник или финансовый директор"}
        </span>
      )}
    </div>
  );
}

function DecisionPanel({
  note,
  setNote,
  onDecide,
  pending,
}: {
  note: string;
  setNote: (v: string) => void;
  onDecide: (d: Decision) => void;
  pending: boolean;
}) {
  return (
    <div className="keycard keycard-active">
      <span className="keycard-label">
        <KeySeg state="off" />
        Ключ 2 · Ваше решение
      </span>
      <div className="decide-list">
        {DECISIONS.map((d) => (
          <button
            key={d.status}
            type="button"
            className={`btn btn-lg decide decide-${d.status}`}
            disabled={pending}
            onClick={() => onDecide(d.status)}
          >
            {d.label}
            <kbd>{d.key}</kbd>
          </button>
        ))}
      </div>
      <textarea className="input" rows={3} placeholder="Комментарий для снабжения, необязательно" value={note} onChange={(e) => setNote(e.target.value)} />
      <span className="muted small">После решения откроется следующий вопрос. Решение можно отменить 6 секунд, потом — вернуть в работу.</span>
    </div>
  );
}

function History({ f }: { f: FindingDetail }) {
  const events = [
    f.decided_at && { at: f.decided_at, text: `${f.decided_by?.full_name ?? "Собственник"}: «${STATUS_TEXT[f.status]}»` },
    f.answered_at && { at: f.answered_at, text: `${f.answered_by?.full_name ?? f.responsible ?? "Снабжение"} ответил` },
    { at: f.created_at, text: "вопрос создан проверкой" },
  ].filter((e): e is { at: string; text: string } => !!e);
  return (
    <div className="history">
      <h3>История</h3>
      <ul>
        {events.map((e) => (
          <li key={e.at + e.text}>
            {dateTime(e.at)} — {e.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnswerBox({ f, queue }: { f: FindingDetail; queue: Finding[] }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const decided = isDecided(f.status);
  const [reasons, setReasons] = useState<Reason[]>((f.answer_reasons ?? []) as Reason[]);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(!f.answer);
  const toggle = (r: Reason) => setReasons((xs) => (xs.includes(r) ? xs.filter((x) => x !== r) : [...xs, r]));
  const body = text.trim() || reasons.join(". ");
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/findings/{finding_id}/answer", {
          params: { path: { finding_id: f.id } },
          body: { answer: f.answer && text.trim() ? `${f.answer}\n${text.trim()}` : body, reasons },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["findings", f.run_id] });
      qc.invalidateQueries({ queryKey: ["finding"] });
      toast({ text: "Ответ отправлен собственнику" });
      const idx = queue.findIndex((x) => x.id === f.id);
      const next = queue[idx + 1] ?? queue.find((x) => x.id !== f.id);
      if (next) navigate(`/findings/${next.id}`);
      else setOpen(false);
    },
    onError: (e) => toast({ kind: "error", text: e.message }),
  });

  if (decided) return null;
  if (!open)
    return (
      <button type="button" className="btn btn-secondary" onClick={() => setOpen(true)}>
        Дополнить ответ
      </button>
    );
  return (
    <form
      className="answer"
      onSubmit={(e) => {
        e.preventDefault();
        if (body.length >= 3) send.mutate();
      }}
    >
      <fieldset className="answer-reasons">
        <legend>
          Причина <span className="muted small">можно несколько</span>
        </legend>
        <div className="chips chips-wrap">
          {REASONS.map((r) => {
            const on = reasons.includes(r);
            return (
              <button key={r} type="button" className={`chip chip-lg${on ? " chip-on" : ""}`} aria-pressed={on} onClick={() => toggle(r)}>
                {on && <Icon name="check" size={14} strokeWidth={2.5} />}
                {r}
              </button>
            );
          })}
        </div>
      </fieldset>
      <label className="field">
        <span className="field-label">{f.answer ? "Дополнение к ответу" : "Ваш ответ"}</span>
        <textarea className="input" rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Почему цена такая: срочность, доставка, марка, условия оплаты…" />
        <span className="muted small">Ответ увидит собственник. Его можно дополнить, пока нет решения.</span>
      </label>
      <button className="btn btn-primary btn-xl" disabled={body.length < 3 || send.isPending}>
        {send.isPending ? "Отправляем…" : "Отправить ответ"}
      </button>
    </form>
  );
}

function Lines({ lines, unit, total, file, signal }: { lines: FindingLine[]; unit: string | null; total: string; file?: string; signal: boolean }) {
  const [all, setAll] = useState(false);
  const u = unitText(unit);
  const volume = lines.reduce((a, l) => a + Number(l.qty_base ?? 0), 0);
  const shown = all ? lines : lines.slice(0, 3);
  return (
    <section className="block">
      <div className="block-head">
        <h2>Строки учёта</h2>
        <span className="muted small">{file ? `файл «${file}»` : count(lines.length, LINES)}</span>
      </div>
      <ul className="lcards mobile-only">
        {shown.map((l) => (
          <li key={l.id}>
            <span className="lcard-row">
              <span className="num">
                {date(l.date)} · <span className="mono">{docNumber(l.doc_no)}</span>
              </span>
              {!signal && <span className="over num">{plus(l.finding_overpayment)}</span>}
            </span>
            <span>{l.item_raw}</span>
            <span className="lcard-row muted small num">
              <span>
                {qty(l.qty_base ?? l.qty)} {u || l.unit_raw} × {int(l.price_base ?? l.price)} сом
              </span>
              <span className="mono">стр. {l.source_row}</span>
            </span>
          </li>
        ))}
        {lines.length > 3 && !all && (
          <li className="lcards-more">
            <button type="button" className="btn btn-link" onClick={() => setAll(true)}>
              Показать все {count(lines.length, LINES)}
            </button>
          </li>
        )}
        {!signal && (
          <li className="lcards-total">
            <span>Итого переплата</span>
            <span className="over num">{int(total)}&nbsp;сом</span>
          </li>
        )}
      </ul>
      <div className="table-wrap desktop-only">
        <table className="table table-tight table-lines">
          <colgroup>
            <col className="c-date" />
            <col className="c-doc" />
            <col className="c-row" />
            <col />
            <col className="c-qty" />
            <col className="c-price" />
            {!signal && <col className="c-over" />}
          </colgroup>
          <thead>
            <tr>
              <th>Дата</th>
              <th>Документ</th>
              <th>Стр.</th>
              <th>Номенклатура в 1С</th>
              <th className="r">Кол-во</th>
              <th className="r">Цена, сом/{u}</th>
              {!signal && <th className="r">Переплата, сом</th>}
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.id}>
                <td className="num">{date(l.date)}</td>
                <td className="mono" title={l.doc_no ?? ""}>
                  {docNumber(l.doc_no)}
                </td>
                <td className="mono muted">{l.source_row}</td>
                <td className="ellipsis" title={l.item_raw}>
                  {l.item_raw}
                </td>
                <td className="r num">
                  {qty(l.qty_base ?? l.qty)} {u}
                </td>
                <td className="r num">{price(l.price_base ?? l.price)}</td>
                {!signal && <td className="r num over">{plus(l.finding_overpayment)}</td>}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={6}>
                Итого · {count(lines.length, LINES)}
                {volume > 0 ? ` · ${qty(volume)} ${u}` : ""}
              </td>
              {!signal && <td className="r num over">{int(total)}</td>}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

// строка вопроса без своей доли (первый из двух одинаковых документов) — прочерк, не «+0»
const plus = (v: string) => (Number(v) > 0 ? `+${int(v)}` : "—");

function rangeText(dates: string[]) {
  if (!dates.length) return "";
  const s = [...dates].sort();
  return `${date(s[0])} — ${date(s[s.length - 1])}`;
}
