// Настройки: пороги правил (собственник и финансы меняют, остальные видят) и люди с ролями.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useLocation } from "react-router";
import { api, unwrap, type Org, type Role } from "../api/client";
import { ErrorNote, Skeleton } from "../components/State";
import { useToast } from "../components/Toast";
import { int } from "../format";
import { ROLE_TEXT, canDecide, canUpload, useRole } from "../session";
import { MobileHead } from "./Layout";
import { AISection, OneCSection } from "./SettingsOneC";

export function SettingsPage() {
  const role = useRole();
  const { hash } = useLocation();
  // ссылка «Подключить 1С» из загрузок ведёт сразу к разделу
  useEffect(() => {
    if (hash) window.setTimeout(() => document.querySelector(hash)?.scrollIntoView({ block: "start" }), 300);
  }, [hash]);
  const org = useQuery({ queryKey: ["org"], queryFn: () => unwrap(api.GET("/api/org")) });
  return (
    <section className="page">
      <MobileHead>
        <span className="mhead-title">Настройки</span>
      </MobileHead>
      <header className="page-head">
        <div>
          <h1 className="page-title">Настройки</h1>
          {org.data && <p className="page-sub">{org.data.name}</p>}
        </div>
      </header>
      {org.isPending ? <Skeleton lines={5} /> : org.isError ? <ErrorNote error={org.error} /> : <Thresholds org={org.data} />}
      <People />
      {org.data && <Network org={org.data} />}
      {canUpload(role) && <OneCSection />}
      {canUpload(role) && <AISection />}
    </section>
  );
}

// Обезличенное сравнение с другими клиентами: даёте свои цены — видите, почём поставщик продаёт другим
function Network({ org }: { org: Org }) {
  const editable = canDecide(useRole());
  const qc = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: (opt_in: boolean) => unwrap(api.PATCH("/api/org/network", { body: { opt_in } })),
    onSuccess: (data) => {
      qc.setQueryData(["org"], data);
      toast({ text: data.network_opt_in ? "Сравнение с другими клиентами включено" : "Сравнение выключено" });
    },
    onError: (e) => toast({ kind: "error", text: e.message }),
  });
  return (
    <section className="block">
      <h2 className="block-title">Сравнение с другими клиентами</h2>
      <div className="panel">
        <label className="check">
          <input
            type="checkbox"
            checked={org.network_opt_in ?? false}
            disabled={!editable || save.isPending}
            onChange={(e) => save.mutate(e.target.checked)}
          />
          Участвовать в обезличенном сравнении цен поставщиков
        </label>
        <p className="muted small">
          Ваши цены попадают в сравнение без названия компании — и только когда по позиции у поставщика есть данные не
          меньше трёх компаний. Взамен вы видите, почём тот же поставщик продаёт ту же позицию другим: среднее, округлённое
          до 10 сом, только прошлые покупки. Отключить можно в любой момент — тогда и сравнение вам не показывается.
        </p>
      </div>
    </section>
  );
}

const pctIn = (v: string) => String(Math.round(Number(v) * 1000) / 10).replace(".", ",");
const pctOut = (v: string) => String(Number(v.replace(",", ".")) / 100);

function Thresholds({ org }: { org: Org }) {
  const editable = canDecide(useRole());
  const qc = useQueryClient();
  const toast = useToast();
  const s = org.settings;
  const [threshold, setThreshold] = useState(String(Number(s.approval_threshold)));
  const [parallel, setParallel] = useState(pctIn(s.parallel_tolerance));
  const [market, setMarket] = useState(pctIn(s.market_tolerance));
  const [pWindow, setPWindow] = useState(String(s.parallel_window_days));
  const [mWindow, setMWindow] = useState(String(s.market_window_days));
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/org/settings", {
          body: {
            approval_threshold: threshold.replace(/\s/g, ""),
            parallel_tolerance: pctOut(parallel),
            market_tolerance: pctOut(market),
            parallel_window_days: Number(pWindow),
            market_window_days: Number(mWindow),
          },
        }),
      ),
    onSuccess: (o) => {
      qc.setQueryData(["org"], o);
      toast({ text: "Пороги сохранены. Они действуют для следующих проверок" });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="panel settings" onSubmit={submit}>
      <h2 className="panel-title">Пороги проверки</h2>
      <Setting
        label="Порог согласования закупки, сом"
        hint={`По нему ловится дробление: серия покупок чуть ниже порога. Сейчас — ${int(s.approval_threshold)} сом.`}
        value={threshold}
        onChange={setThreshold}
        disabled={!editable}
        inputMode="numeric"
      />
      <Setting
        label="Допуск к цене другого поставщика, %"
        hint="Дороже параллельного поставщика в пределах допуска — не вопрос."
        value={parallel}
        onChange={setParallel}
        disabled={!editable}
        inputMode="decimal"
      />
      <Setting
        label="Допуск к рынку, %"
        hint="Цена выше рыночной медианы меньше чем на допуск — не вопрос."
        value={market}
        onChange={setMarket}
        disabled={!editable}
        inputMode="decimal"
      />
      <details className="more">
        <summary>Дополнительно</summary>
        <Setting label="Окно сравнения с другими поставщиками, дней" hint="7–120 дней" value={pWindow} onChange={setPWindow} disabled={!editable} inputMode="numeric" />
        <Setting label="Окно поиска рыночных цен, дней" hint="7–180 дней" value={mWindow} onChange={setMWindow} disabled={!editable} inputMode="numeric" />
      </details>
      <p className="muted small">Новые пороги действуют для следующих проверок; прошлые результаты не меняются.</p>
      {editable ? (
        <button className="btn btn-primary" disabled={save.isPending}>
          Сохранить пороги
        </button>
      ) : (
        <p className="muted small">Пороги меняют собственник и финансовый директор.</p>
      )}
      {save.error && <ErrorNote error={save.error} />}
    </form>
  );
}

function Setting(props: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  inputMode: "numeric" | "decimal";
}) {
  return (
    <label className="field">
      <span className="field-label">{props.label}</span>
      <input className="input" inputMode={props.inputMode} value={props.value} disabled={props.disabled} onChange={(e) => props.onChange(e.target.value)} />
      <span className="muted small">{props.hint}</span>
    </label>
  );
}

const ROLES: Role[] = ["owner", "finance", "accountant", "buyer", "viewer"];

function People() {
  const role = useRole();
  const members = useQuery({ queryKey: ["members"], queryFn: () => unwrap(api.GET("/api/org/members")) });
  const [inviting, setInviting] = useState(false);
  return (
    <section className="block">
      <div className="block-head">
        <h2>Люди и роли</h2>
        {role === "owner" && !inviting && (
          <button type="button" className="btn btn-secondary" onClick={() => setInviting(true)}>
            Пригласить
          </button>
        )}
      </div>
      {inviting && <Invite onDone={() => setInviting(false)} />}
      {members.isPending ? (
        <Skeleton lines={4} />
      ) : members.isError ? (
        <ErrorNote error={members.error} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Имя</th>
                <th>Email</th>
                <th>Роль</th>
                <th>Как записан в 1С</th>
              </tr>
            </thead>
            <tbody>
              {members.data.map((m) => (
                <tr key={m.user.id}>
                  <td>{m.user.full_name}</td>
                  <td className="muted">{m.user.email}</td>
                  <td>{ROLE_TEXT[m.role]}</td>
                  <td className={m.alias ? "" : "muted"}>{m.alias ?? (m.role === "buyer" ? "не указано — не увидит вопросов" : "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Invite({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ email: "", full_name: "", role: "buyer" as Role, alias: "", password: "" });
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const invite = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/org/members", {
          body: { ...form, alias: form.alias.trim() || null, password: form.password || null },
        }),
      ),
    onSuccess: (m) => {
      qc.invalidateQueries({ queryKey: ["members"] });
      toast({ text: `${m.user.full_name} добавлен(а) как «${ROLE_TEXT[m.role]}»` });
      onDone();
    },
  });
  return (
    <form
      className="panel invite"
      onSubmit={(e) => {
        e.preventDefault();
        invite.mutate();
      }}
    >
      <label className="field">
        <span className="field-label">Email</span>
        <input className="input" type="email" required value={form.email} onChange={(e) => set("email")(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Имя</span>
        <input className="input" required value={form.full_name} onChange={(e) => set("full_name")(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Роль</span>
        <select className="input" value={form.role} onChange={(e) => set("role")(e.target.value)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_TEXT[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Как записан в 1С{form.role === "buyer" ? " — обязательно" : ""}</span>
        <input className="input" placeholder="Асанов Б." required={form.role === "buyer"} value={form.alias} onChange={(e) => set("alias")(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Временный пароль</span>
        <input className="input" type="text" minLength={10} placeholder="не короче 10 символов" value={form.password} onChange={(e) => set("password")(e.target.value)} />
        <span className="muted small">Нужен, если человека ещё нет в системе. Передайте его лично.</span>
      </label>
      <div className="actions">
        <button className="btn btn-primary" disabled={invite.isPending}>
          Добавить
        </button>
        <button type="button" className="btn btn-link" onClick={onDone}>
          Отмена
        </button>
      </div>
      {invite.error && <ErrorNote error={invite.error} />}
    </form>
  );
}
