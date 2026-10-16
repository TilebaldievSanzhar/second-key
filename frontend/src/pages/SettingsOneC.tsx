// Настройки → Подключение 1С: токены интеграций, обработка выгрузки, OData-подключения, MCP для ИИ-агентов.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { Notice, ErrorNote, Skeleton } from "../components/State";
import { useToast } from "../components/Toast";
import { dayTime } from "../format";
import { useCapabilities } from "../session";

type Conn = Schemas["ConnectionOut"];
const API = `${window.location.origin}/api`;

export function OneCSection() {
  return (
    <section className="block" id="onec">
      <h2 className="block-title">Подключение 1С</h2>
      <p className="muted">
        Три способа, от простого к гибкому: обработка в самой 1С отправляет строки по токену (публиковать базу не нужно);
        «Второй ключ» сам забирает документы по OData с опубликованной базы; ИИ-агент с MCP-сервером 1С передаёт строки
        через MCP «Второго ключа».
      </p>
      <Tokens />
      <ModuleInfo />
      <Connections />
    </section>
  );
}

function Tokens() {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["tokens"], queryFn: () => unwrap(api.GET("/api/integrations/tokens")) });
  const [name, setName] = useState("1С: выгрузка закупок");
  const [push, setPush] = useState(true);
  const [mcp, setMcp] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/integrations/tokens", { body: { name, scopes: [...(push ? ["push"] : []), ...(mcp ? ["mcp"] : [])] as ("push" | "mcp")[] } })),
    onSuccess: (t) => {
      setFresh(t.token);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.DELETE("/api/integrations/tokens/{token_id}", { params: { path: { token_id: id } } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tokens"] });
      toast({ text: "Токен отозван" });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <div className="panel">
      <h3 className="panel-title">Токены интеграций</h3>
      <form className="actions" onSubmit={submit}>
        <label className="field">
          <span className="field-label">Название</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="check">
          <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} /> Выгрузка из 1С
        </label>
        <label className="check">
          <input type="checkbox" checked={mcp} onChange={(e) => setMcp(e.target.checked)} /> MCP для ИИ-агентов
        </label>
        <button className="btn btn-primary" disabled={create.isPending || (!push && !mcp)}>
          Создать токен
        </button>
      </form>
      {create.error && <ErrorNote error={create.error} />}
      {fresh && (
        <div className="token-once" role="status">
          <strong>Скопируйте токен сейчас — потом его не показать.</strong>
          <pre className="code">{fresh}</pre>
          <div className="actions">
            <button type="button" className="btn btn-secondary" onClick={() => navigator.clipboard?.writeText(fresh).then(() => toast({ text: "Токен скопирован" }))}>
              Скопировать
            </button>
            <button type="button" className="btn btn-link" onClick={() => setFresh(null)}>
              Готово
            </button>
          </div>
          <span className="muted small">В обработке 1С: адрес сервиса {API}, токен — этот. Проверка из командной строки:</span>
          <pre className="code">{`curl -H "Authorization: Bearer ${fresh}" ${API}/integrations/ping`}</pre>
          {mcp && (
            <>
              <span className="muted small">MCP для Claude, Cursor и других агентов (.mcp.json):</span>
              <pre className="code">
                {JSON.stringify({ mcpServers: { "second-key": { type: "http", url: `${API}/mcp/`, headers: { Authorization: `Bearer ${fresh}` } } } }, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}
      {list.isPending ? (
        <Skeleton lines={2} />
      ) : list.data && list.data.length === 0 ? (
        <p className="muted small">Токенов пока нет — создайте первый: для обработки 1С или для ИИ-агента.</p>
      ) : list.data && list.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Название</th>
                <th>Токен</th>
                <th>Права</th>
                <th>Использовался</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.data.map((t) => (
                <tr key={t.id} className={t.revoked_at ? "muted" : ""}>
                  <td>{t.name}</td>
                  <td className="mono">{t.prefix}…</td>
                  <td>{t.scopes.map((x) => (x === "push" ? "выгрузка" : "MCP")).join(", ")}</td>
                  <td className="muted">{t.revoked_at ? "отозван" : t.last_used_at ? dayTime(t.last_used_at) : "ещё нет"}</td>
                  <td className="r">
                    {!t.revoked_at && (
                      <button type="button" className="btn btn-link" onClick={() => revoke.mutate(t.id)}>
                        Отозвать
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ModuleInfo() {
  return (
    <div className="panel">
      <h3 className="panel-title">Обработка «Второй ключ: выгрузка закупок»</h3>
      <p className="muted">
        Работает в любой базе: соединение исходящее, из 1С в сервис. Поддерживает БП 3.0 и УТ 11 / ERP / КА 2 —
        конфигурация определяется сама.
      </p>
      <ol className="steps-list">
        <li>Скачайте модуль и передайте программисту 1С — в начале файла инструкция, как собрать обработку.</li>
        <li>Создайте токен с правом «Выгрузка из 1С».</li>
        <li>В обработке укажите адрес сервиса {API} и токен, нажмите «Проверить связь», затем «Выгрузить».</li>
      </ol>
      <div className="actions">
        <a className="btn btn-secondary" href="/api/integrations/1c/module.bsl" download>
          Скачать модуль обработки (.bsl)
        </a>
      </div>
    </div>
  );
}

const EMPTY = { name: "1С: Бухгалтерия", base_url: "", username: "", password: "", profile: "bp30" as "bp30" | "ut11", period_days: 90, schedule: "manual" as "manual" | "daily" };


function Connections() {
  const caps = useCapabilities();
  const noSecrets = caps.data?.secrets === false;
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["connections"], queryFn: () => unwrap(api.GET("/api/integrations/connections")) });
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [test, setTest] = useState<Schemas["ConnectionTestOut"] | null>(null);
  const set = <K extends keyof typeof EMPTY>(k: K, v: (typeof EMPTY)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const check = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/integrations/connections/test", {
        params: { query: { connection_id: editing ?? undefined } },
        body: { ...form, password: form.password || null },
      })),
    onSuccess: (r) => {
      setTest(r);
      if (r.ok && r.profile) set("profile", r.profile as "bp30" | "ut11");
    },
  });
  const save = useMutation({
    mutationFn: () =>
      editing
        ? unwrap(api.PATCH("/api/integrations/connections/{connection_id}", { params: { path: { connection_id: editing } }, body: { ...form, password: form.password || null } }))
        : unwrap(api.POST("/api/integrations/connections", { body: { ...form, password: form.password || null } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connections"] });
      toast({ text: "Подключение сохранено" });
      setOpen(false);
      setEditing(null);
      setForm(EMPTY);
      setTest(null);
    },
  });
  const sync = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/integrations/connections/{connection_id}/sync", { params: { path: { connection_id: id } }, body: {} })),
    onSuccess: () => {
      toast({ text: "Забираем строки из 1С — проверка появится в «Загрузках»" });
      window.setTimeout(() => qc.invalidateQueries({ queryKey: ["connections"] }), 3000);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.DELETE("/api/integrations/connections/{connection_id}", { params: { path: { connection_id: id } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connections"] }),
  });
  const edit = (c: Conn) => {
    setEditing(c.id);
    setForm({ name: c.name, base_url: c.base_url ?? "", username: c.username ?? "", password: "", profile: (c.profile as "bp30" | "ut11") ?? "bp30", period_days: c.period_days, schedule: c.schedule as "manual" | "daily" });
    setOpen(true);
    setTest(null);
  };

  return (
    <div className="panel">
      <div className="block-head">
        <h3 className="panel-title">OData: «Второй ключ» забирает документы сам</h3>
        {!open && (
          <button type="button" className="btn btn-secondary" disabled={noSecrets} onClick={() => { setOpen(true); setEditing(null); setForm(EMPTY); }}>
            Добавить подключение
          </button>
        )}
      </div>
      <p className="muted">
        Для опубликованной базы (веб-сервер, 1С:Fresh, облако). В публикации OData включите документ поступления и справочники
        контрагентов, номенклатуры, единиц, складов и пользователей; заведите пользователя только с правом чтения.
      </p>
      {noSecrets && (
        <Notice>
          Сохранять пароли 1С пока нельзя: на сервере не задан ключ шифрования (SK_DATA_KEY). Попросите администратора
          сервиса добавить его, а до этого используйте обработку выгрузки — ей пароль не нужен.
        </Notice>
      )}
      {open && (
        <form
          className="settings"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <label className="field">
            <span className="field-label">Название</span>
            <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} required />
          </label>
          <label className="field">
            <span className="field-label">Адрес публикации базы</span>
            <input className="input" placeholder="https://1c.example.kg/bp" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} required />
          </label>
          <label className="field">
            <span className="field-label">Пользователь 1С</span>
            <input className="input" value={form.username} onChange={(e) => set("username", e.target.value)} required autoComplete="off" />
          </label>
          <label className="field">
            <span className="field-label">Пароль{editing ? " — пусто, чтобы оставить прежний" : ""}</span>
            <input className="input" type="password" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" />
          </label>
          <label className="field">
            <span className="field-label">Конфигурация</span>
            <select className="input" value={form.profile} onChange={(e) => set("profile", e.target.value as "bp30" | "ut11")}>
              <option value="bp30">1С:Бухгалтерия 3.0</option>
              <option value="ut11">УТ 11, ERP, КА 2</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Сколько дней забирать</span>
            <input className="input" type="number" min={7} max={730} value={form.period_days} onChange={(e) => set("period_days", Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field-label">Расписание</span>
            <select className="input" value={form.schedule} onChange={(e) => set("schedule", e.target.value as "manual" | "daily")}>
              <option value="manual">Вручную</option>
              <option value="daily">Каждый день в 6:00 — контроль цен</option>
            </select>
          </label>
          {test && (test.ok ? <Notice tone="accent">Подключение работает: {test.title}.</Notice> : <Notice>{test.error}</Notice>)}
          {(check.error || save.error) && <ErrorNote error={check.error ?? save.error} />}
          <div className="actions">
            <button type="button" className="btn btn-secondary" disabled={check.isPending || !form.base_url} onClick={() => check.mutate()}>
              {check.isPending ? "Проверяем…" : "Проверить"}
            </button>
            <button className="btn btn-primary" disabled={save.isPending}>
              Сохранить
            </button>
            <button type="button" className="btn btn-link" onClick={() => { setOpen(false); setEditing(null); setTest(null); }}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {list.isPending ? (
        <Skeleton lines={2} />
      ) : list.data && list.data.length === 0 && !open ? (
        <p className="muted small">Подключений пока нет.</p>
      ) : list.data && list.data.length > 0 ? (
        <ul className="conn-list">
          {list.data.map((c) => (
            <li key={c.id} className="filecard">
              <div className="filecard-main">
                <span className="filecard-name">{c.name}</span>
                <span className="muted small">
                  {c.base_url} · {c.schedule === "daily" ? "каждый день" : "вручную"} ·{" "}
                  {c.last_sync_at ? `${c.last_status === "ok" ? "забрали" : "ошибка"} ${dayTime(c.last_sync_at)}` : "ещё не забирали"}
                </span>
                {c.last_status === "error" && <span className="err small">{c.last_error}</span>}
              </div>
              <div className="actions">
                <button type="button" className="btn btn-primary" disabled={sync.isPending} onClick={() => sync.mutate(c.id)}>
                  Забрать сейчас
                </button>
                <button type="button" className="btn btn-link" onClick={() => edit(c)}>
                  Изменить
                </button>
                <button type="button" className="btn btn-link" onClick={() => remove.mutate(c.id)}>
                  Удалить
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ИИ-подсказки: что включено на сервере и что без них
export function AISection() {
  const caps = useCapabilities();
  if (!caps.data) return null;
  return (
    <section className="block">
      <h2 className="block-title">ИИ-подсказки</h2>
      <div className="panel">
        {caps.data.llm ? (
          <p>
            Включены: ИИ предлагает сопоставление колонок для нестандартных выгрузок, распознаёт номенклатуру, которую не поняли
            правила, проверяет ответы снабжения и пишет текст для WhatsApp. Суммы и решения — всегда за системой и людьми.
          </p>
        ) : (
          <p>
            Выключены: колонки и номенклатура распознаются правилами, текст для WhatsApp собирается по шаблону. Включает
            администратор сервиса (ключ SK_LLM_API_KEY).
          </p>
        )}
      </div>
    </section>
  );
}
