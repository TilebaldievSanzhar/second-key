// Карточка поставщика: что известно по ИНН и реквизитам, через кого покупали, цены по позициям против
// других поставщиков и рынка, следы и вопросы. Собственнику и финансам.
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import { ApiError, api, unwrap, type SupplierDetail } from "../api/client";
import { FindingRow } from "../components/FindingRow";
import { Icon } from "../components/Icon";
import { Money } from "../components/Money";
import { PaymentsBlock, RegistryBlock } from "../components/SupplierBlocks";
import { Empty, ErrorNote, Skeleton } from "../components/State";
import { TraceGroups, TraceList } from "../components/Traces";
import { LINES, count, date, dayMonth, int, moneyShort, pct, share, signedShare, unit as unitText } from "../format";
import { GROUPS } from "../questions";
import { canSeeLinks, useCapabilities, useCurrentRun, useRole, useRunFindings } from "../session";
import { MobileHead } from "./Layout";

export function SupplierPage() {
  const role = useRole();
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const { run } = useCurrentRun();
  const runId = params.get("run") ?? run?.id;
  const q = useQuery({
    queryKey: ["supplier", runId, id],
    enabled: !!runId && canSeeLinks(role),
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}/suppliers/{supplier_id}", { params: { path: { run_id: runId!, supplier_id: id } } })),
  });
  if (!canSeeLinks(role)) return <Navigate to="/" replace />;
  return (
    <section className="page supplier-page">
      <MobileHead>
        <span className="mhead-title">Поставщик</span>
      </MobileHead>
      <div className="card-top">
        <Link to="/links" className="back">
          <Icon name="back" size={20} strokeWidth={2} />
          Связи
        </Link>
      </div>
      {!runId || q.isPending ? (
        <Skeleton variant="card" />
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 404 ? (
          <Empty title="Поставщик не найден в этой проверке">
            Карточки поставщиков — у каждой проверки свои. Откройте поставщика из раздела «Связи» нужной проверки.
          </Empty>
        ) : (
          <ErrorNote error={q.error} retry={() => q.refetch()} />
        )
      ) : (
        <Card s={q.data} runId={runId} />
      )}
    </section>
  );
}

function innLine(s: SupplierDetail): string {
  if (!s.inn) return "ИНН в выгрузке нет";
  if (s.inn_kind === "org" && s.inn_date) return `Юрлицо · зарегистрировано ${date(s.inn_date)} (по номеру ИНН) · ИНН ${s.inn}`;
  if (s.inn_kind === "person" && s.inn_date) {
    const years = Math.floor((new Date(s.first_date).getTime() - new Date(s.inn_date).getTime()) / (365.25 * 864e5));
    return `ИП · ${years} ${years % 10 === 1 && years % 100 !== 11 ? "год" : years % 10 >= 2 && years % 10 <= 4 && (years % 100 < 12 || years % 100 > 14) ? "года" : "лет"} на дату первой поставки (по номеру ИНН) · ИНН ${s.inn}`;
  }
  return `ИНН ${s.inn}`;
}

const FIELD: Record<string, string> = { phone: "телефон", bank_account: "расчётный счёт", email: "e-mail", address: "адрес" };

function Card({ s, runId }: { s: SupplierDetail; runId: string }) {
  const findings = useRunFindings(runId);
  const caps = useCapabilities();
  const mine = (findings.data ?? []).filter((f) => f.supplier === s.name);
  const groups = GROUPS.filter((g) => s.traces.some((t) => t.group === g.key)).map((g) => g.key);
  const top = s.buyers[0];
  const totalBuyers = s.buyers.reduce((a, b) => a + Number(b.spend), 0);
  return (
    <div className="supplier">
      <header className="q-head">
        <p className="q-rule">Поставщик</p>
        <h1 className="q-title">{s.name}</h1>
        <p className="q-text">{innLine(s)}</p>
      </header>

      <div className="facts">
        <div className="fact fact-money">
          <span className="fact-label">Оборот за период</span>
          <Money value={s.spend} size="xl" />
          <span className="fact-sub">{share(s.share)} ваших закупок</span>
        </div>
        <div className="fact">
          <span className="fact-label">Покупки</span>
          <span className="fact-value">{count(s.lines, LINES)}</span>
          <span className="fact-sub">
            {dayMonth(s.first_date)} — {date(s.last_date)}
          </span>
        </div>
        <div className="fact">
          <span className="fact-label">Позиций</span>
          <span className="fact-value">{s.skus}</span>
          <span className="fact-sub">материалов в каталоге</span>
        </div>
        <div className="fact desktop-only">
          <span className="fact-label">Основной снабженец</span>
          <span className="fact-value">{top?.name ?? "не указан"}</span>
          <span className="fact-sub">{top ? `${pct(top.spend, totalBuyers)} оборота` : ""}</span>
        </div>
      </div>

      {s.linked && (
        <p className="note note-over">
          <Icon name="link" size={18} strokeWidth={2} />
          <span>
            По поставщику задан вопрос-связка: следы сошлись в разных группах.{" "}
            {mine.find((f) => f.code === "LINK") && <Link to={`/findings/${mine.find((f) => f.code === "LINK")!.id}`}>Открыть вопрос</Link>}
          </span>
        </p>
      )}

      <section className="block">
        <div className="block-head">
          <h2>Следы</h2>
          {groups.length > 0 && <TraceGroups groups={groups} />}
        </div>
        {s.traces.length ? (
          <TraceList traces={s.traces} />
        ) : (
          <p className="muted">Следов связки нет: цены, поведение и реквизиты поставщика без особенностей.</p>
        )}
      </section>

      <RegistryBlock s={s} runId={runId} provider={caps.data?.registry || undefined} />
      <PaymentsBlock s={s} />

      {s.items.length > 0 && (
        <section className="block">
          <div className="block-head">
            <h2>Цены по позициям</h2>
            <span className="muted small">медианы за базовую единицу, в те же месяцы</span>
          </div>
          <div className="table-wrap">
            <table className="table table-tight">
              <thead>
                <tr>
                  <th>Позиция</th>
                  <th className="r">Покупок</th>
                  <th className="r">У поставщика</th>
                  <th className="r">У других</th>
                  <th className="r">Разница</th>
                  <th className="r desktop-cell">Рынок</th>
                  <th className="r desktop-cell">Разница</th>
                </tr>
              </thead>
              <tbody>
                {s.items.map((i) => (
                  <tr key={i.sku}>
                    <td className="ellipsis" title={i.label}>
                      {i.label}
                      <span className="muted small"> · сом/{unitText(i.unit)}</span>
                    </td>
                    <td className="r num">{i.lines}</td>
                    <td className="r num">{int(i.price)}</td>
                    <td className="r num" title={i.others_suppliers ? `${i.others_suppliers} поставщ.` : undefined}>
                      {i.others ? int(i.others) : "—"}
                    </td>
                    <td className={`r num${Number(i.vs_others ?? 0) >= 0.05 ? " over" : ""}`}>{i.vs_others === null ? "—" : signedShare(i.vs_others)}</td>
                    <td className="r num desktop-cell">{i.market ? int(i.market) : "—"}</td>
                    <td className={`r num desktop-cell${Number(i.vs_market ?? 0) >= 0.1 ? " over" : ""}`}>
                      {i.vs_market === null ? "—" : signedShare(i.vs_market)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="supplier-grid">
        <section className="block">
          <h2 className="block-title">Через кого покупали</h2>
          <ul className="cut cut-card">
            {s.buyers.map((b) => (
              <li key={b.name}>
                <span className="cut-row">
                  <span className="cut-label">{b.name}</span>
                  <span className="cut-amt">{moneyShort(b.spend)}</span>
                  <span className="cut-note">{count(b.lines, LINES)}</span>
                  <span className="cut-bar" aria-hidden="true">
                    <span style={{ width: `${(Number(b.spend) / Math.max(totalBuyers, 1)) * 100}%` }} />
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="block">
          <h2 className="block-title">Реквизиты</h2>
          {s.card && (s.card.phone || s.card.email || s.card.address || s.card.bank_account) ? (
            <dl className="reqs">
              {(["phone", "email", "address", "bank_account"] as const).map((k) =>
                s.card?.[k] ? (
                  <div key={k}>
                    <dt>{FIELD[k]}</dt>
                    <dd className={k === "bank_account" || k === "phone" ? "mono" : undefined}>{s.card[k]}</dd>
                  </div>
                ) : null,
              )}
            </dl>
          ) : (
            <p className="muted small">Реквизитов в выгрузке нет. Обработка 1С передаёт их из карточки контрагента.</p>
          )}
          {s.shared.map((x) => (
            <p key={x.other ?? ""} className="note note-over">
              <Icon name="alert" size={18} strokeWidth={2} />
              <span>
                {x.fields.map((f) => FIELD[f] ?? f).join(", ")} совпадает с{" "}
                {x.other_id ? <Link to={`/suppliers/${x.other_id}?run=${runId}`}>{x.other}</Link> : x.other}
              </span>
            </p>
          ))}
        </section>
      </div>

      <section className="block">
        <div className="block-head">
          <h2>Вопросы по поставщику</h2>
          <Link to={`/findings?supplier=${encodeURIComponent(s.name)}&tab=all`}>Во входящих</Link>
        </div>
        {mine.length ? (
          <ul className="flist">
            {mine.map((f) => (
              <FindingRow key={f.id} f={f} showRule />
            ))}
          </ul>
        ) : (
          <p className="muted">Вопросов по этому поставщику нет.</p>
        )}
      </section>
    </div>
  );
}
