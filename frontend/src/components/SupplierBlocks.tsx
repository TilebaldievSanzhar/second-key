// Карточка поставщика: сведения реестра (ручная проверка по выписке или агрегатор) и как ему платят.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, unwrap, type SupplierDetail } from "../api/client";
import { count, date, docNumber, int, money, moneyShort, share } from "../format";
import { useToast } from "./Toast";

const STATUSES = ["Действующее", "В процессе ликвидации", "Ликвидировано", "Прекращена деятельность ИП", "Банкротство"];
const SOURCE: Record<string, string> = { manual: "ручная проверка по выписке", statsnet: "Statsnet" };

export function RegistryBlock({ s, runId, provider }: { s: SupplierDetail; runId: string; provider?: string }) {
  const rec = s.registry;
  const [edit, setEdit] = useState(false);
  const person = s.inn_kind === "person";
  return (
    <section className="block">
      <div className="block-head">
        <h2>Реестр</h2>
        {rec && (
          <span className="muted small">
            {SOURCE[rec.source] ?? rec.source}
            {rec.checked_at ? ` · ${date(rec.checked_at)}` : ""}
          </span>
        )}
      </div>
      {rec ? (
        <dl className="reqs">
          <Row k="статус" v={rec.status ?? (rec.active === false ? "недействующее" : rec.active ? "действующее" : null)} warn={rec.active === false} />
          <Row k="регистрация" v={rec.registered ? date(rec.registered) : null} />
          <Row k="руководитель" v={rec.director} />
          <Row k="учредители" v={rec.founders.length ? rec.founders.join(", ") : null} />
          <Row k="адрес" v={rec.address} />
          {rec.unreliable && <Row k="госзакупки" v="в реестре недобросовестных поставщиков" warn />}
          {rec.risks.length > 0 && <Row k="риски" v={rec.risks.join("; ")} warn />}
          {rec.url && (
            <div>
              <dt>источник</dt>
              <dd>
                <a href={rec.url} target="_blank" rel="noreferrer">
                  {rec.url.replace(/^https?:\/\//, "").slice(0, 60)}
                </a>
              </dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="muted">
          Сведений реестра нет.{" "}
          {person
            ? "ИП нет в реестре юрлиц Минюста — статус проверяют в налоговой (salyk.kg) или по свидетельству ИП."
            : `Найдите поставщика по ИНН ${s.inn ?? ""} на record.minjust.gov.kg и внесите главное из выписки.`}
          {provider ? ` Или запросите у ${provider} в разделе «Связи».` : ""}
        </p>
      )}
      {s.inn &&
        (edit ? (
          <RegistryForm s={s} runId={runId} onDone={() => setEdit(false)} />
        ) : (
          <div className="actions">
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEdit(true)}>
              {rec?.source === "manual" ? "Изменить сведения" : "Внести по выписке"}
            </button>
            {!person && (
              <a className="btn btn-link" href="https://record.minjust.gov.kg" target="_blank" rel="noreferrer">
                Реестр Минюста
              </a>
            )}
          </div>
        ))}
    </section>
  );
}

function Row({ k, v, warn = false }: { k: string; v: string | null | undefined; warn?: boolean }) {
  if (!v) return null;
  return (
    <div>
      <dt>{k}</dt>
      <dd className={warn ? "reg-warn" : undefined}>{v}</dd>
    </div>
  );
}

function RegistryForm({ s, runId, onDone }: { s: SupplierDetail; runId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const rec = s.registry?.source === "manual" ? s.registry : null;
  const [status, setStatus] = useState(rec?.status ?? "");
  const [registered, setRegistered] = useState(rec?.registered ?? "");
  const [director, setDirector] = useState(rec?.director ?? "");
  const [founders, setFounders] = useState((rec?.founders ?? []).join(", "));
  const [address, setAddress] = useState(rec?.address ?? "");
  const [unreliable, setUnreliable] = useState(!!rec?.unreliable);
  const [url, setUrl] = useState(rec?.url ?? "");
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/audits/{run_id}/suppliers/{supplier_id}/registry", {
          params: { path: { run_id: runId, supplier_id: s.id } },
          body: {
            status: status || null,
            active: status ? status === "Действующее" : null,
            registered: registered || null,
            director: director.trim() || null,
            founders: founders
              .split(/[,;\n]/)
              .map((x) => x.trim())
              .filter(Boolean),
            address: address.trim() || null,
            unreliable: unreliable || null,
            url: url.trim() || null,
          },
        }),
      ),
    onSuccess: (data) => {
      qc.setQueryData(["supplier", runId, s.id], data);
      for (const key of ["links", "suppliers", "findings", "finding"]) qc.invalidateQueries({ queryKey: [key] });
      toast({ text: "Сведения сохранены — связки проверки пересчитаны" });
      onDone();
    },
    onError: (e) => toast({ kind: "error", text: e.message }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="panel reg-form" onSubmit={submit}>
      <label className="field">
        <span className="field-label">Статус по выписке</span>
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">не указан</option>
          {STATUSES.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Дата регистрации</span>
        <input className="input" type="date" value={registered} onChange={(e) => setRegistered(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Руководитель</span>
        <input className="input" value={director} onChange={(e) => setDirector(e.target.value)} placeholder="Фамилия Имя Отчество" />
      </label>
      <label className="field">
        <span className="field-label">Учредители</span>
        <input className="input" value={founders} onChange={(e) => setFounders(e.target.value)} placeholder="через запятую" />
      </label>
      <label className="field reg-wide">
        <span className="field-label">Юридический адрес</span>
        <input className="input" value={address} onChange={(e) => setAddress(e.target.value)} />
      </label>
      <label className="field reg-wide">
        <span className="field-label">Ссылка на выписку или источник</span>
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://record.minjust.gov.kg/…" />
      </label>
      <label className="check reg-wide">
        <input type="checkbox" checked={unreliable} onChange={(e) => setUnreliable(e.target.checked)} />В реестре недобросовестных поставщиков госзакупок
      </label>
      <p className="muted small reg-wide">
        Сведения видны только вашей организации. После сохранения следы и вопросы-связки этой проверки пересчитываются;
        ответы снабжения и ваши решения остаются.
      </p>
      <div className="actions reg-wide">
        <button className="btn btn-primary" disabled={save.isPending}>
          {save.isPending ? "Сохраняем…" : "Сохранить"}
        </button>
        <button type="button" className="btn btn-link" onClick={onDone}>
          Отмена
        </button>
      </div>
    </form>
  );
}

export function PaymentsBlock({ s }: { s: SupplierDetail }) {
  const p = s.payment;
  if (!p) return null;
  const advance = Number(p.advance) > 0;
  return (
    <section className="block">
      <div className="block-head">
        <h2>Оплаты</h2>
        <span className="muted small">списания с расчётного счёта и кассы с видом «оплата поставщику»</span>
      </div>
      <div className="facts facts-pay">
        <div className="fact">
          <span className="fact-label">Оплачено</span>
          <span className="fact-value">{moneyShort(p.paid)}</span>
          <span className="fact-sub">{count(p.payments, PAYMENTS)} · поставлено на {moneyShort(p.delivered)}</span>
        </div>
        <div className="fact">
          <span className="fact-label">По предоплате</span>
          <span className="fact-value">{p.prepay_share === null ? "—" : share(p.prepay_share)}</span>
          <span className="fact-sub">поставок оплачено заранее</span>
        </div>
        <div className="fact">
          <span className="fact-label">Срок оплаты</span>
          <span className="fact-value">{p.delay_days === null ? "—" : `${Math.round(Number(p.delay_days))} дн.`}</span>
          <span className="fact-sub">в среднем после поставки</span>
        </div>
        <div className="fact">
          <span className="fact-label">Аванс без поставки</span>
          <span className={`fact-value${advance ? " over" : ""}`}>{advance ? money(p.advance) : "нет"}</span>
          <span className="fact-sub">{advance && p.advance_since ? `с ${date(p.advance_since)}` : "на конец периода"}</span>
        </div>
      </div>
      {s.payments_list.length > 0 && (
        <div className="table-wrap">
          <table className="table table-tight">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Документ</th>
                <th className="r">Сумма, сом</th>
                <th className="desktop-cell">Назначение</th>
              </tr>
            </thead>
            <tbody>
              {s.payments_list.map((x, i) => (
                <tr key={x.date + i}>
                  <td className="num">{date(x.date)}</td>
                  <td className="mono">{docNumber(x.doc_no)}</td>
                  <td className="r num">{int(x.amount)}</td>
                  <td className="desktop-cell ellipsis" title={x.purpose ?? ""}>
                    {x.purpose ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {s.payments_list.length >= 30 && <p className="muted small">Последние 30 оплат.</p>}
    </section>
  );
}

const PAYMENTS: [string, string, string] = ["оплата", "оплаты", "оплат"];
