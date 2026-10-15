import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, unwrap, type Org } from "../api/client";
import { ErrorNote, Loading } from "../components/State";
import { canDecide, useRole } from "../session";

const ROLE_LABEL: Record<string, string> = {
  owner: "Собственник",
  finance: "Финансы",
  accountant: "Бухгалтерия",
  buyer: "Снабжение",
  viewer: "Просмотр",
};

export function SettingsPage() {
  const org = useQuery({ queryKey: ["org"], queryFn: () => unwrap(api.GET("/api/org")) });
  const members = useQuery({ queryKey: ["members"], queryFn: () => unwrap(api.GET("/api/org/members")) });
  if (org.isPending) return <Loading />;
  if (org.isError) return <ErrorNote error={org.error} />;
  return (
    <section>
      <h2>Настройки · {org.data.name}</h2>
      <Thresholds org={org.data} />
      <h3>Люди и роли</h3>
      {members.data && (
        <table className="table">
          <tbody>
            {members.data.map((m) => (
              <tr key={m.user.id}>
                <td>{m.user.full_name}</td>
                <td>{m.user.email}</td>
                <td>{ROLE_LABEL[m.role]}</td>
                <td className="muted">{m.alias ? "в 1С: " + m.alias : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Thresholds({ org }: { org: Org }) {
  const editable = canDecide(useRole());
  const qc = useQueryClient();
  const s = org.settings;
  const [threshold, setThreshold] = useState(String(Number(s.approval_threshold)));
  const [parallel, setParallel] = useState(String(Number(s.parallel_tolerance) * 100));
  const [market, setMarket] = useState(String(Number(s.market_tolerance) * 100));
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/org/settings", {
          body: {
            approval_threshold: threshold,
            parallel_tolerance: String(Number(parallel) / 100),
            market_tolerance: String(Number(market) / 100),
          },
        }),
      ),
    onSuccess: (o) => qc.setQueryData(["org"], o),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="panel settings" onSubmit={submit}>
      <label>
        Порог согласования закупки, сом
        <input inputMode="numeric" value={threshold} disabled={!editable} onChange={(e) => setThreshold(e.target.value)} />
      </label>
      <label>
        Допуск к цене другого поставщика, %
        <input inputMode="decimal" value={parallel} disabled={!editable} onChange={(e) => setParallel(e.target.value)} />
      </label>
      <label>
        Допуск к рынку, %
        <input inputMode="decimal" value={market} disabled={!editable} onChange={(e) => setMarket(e.target.value)} />
      </label>
      <p className="muted">Новые пороги действуют для следующих проверок; прошлые результаты не меняются.</p>
      {editable && (
        <button className="primary" disabled={save.isPending}>
          Сохранить
        </button>
      )}
      {save.isSuccess && (
        <span className="muted" role="status">
          Сохранено
        </span>
      )}
      {save.error && <ErrorNote error={save.error} />}
    </form>
  );
}
