import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import { Icon } from "../components/Icon";
import { BrandMark } from "../components/RunPicker";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const login = useMutation({
    mutationFn: () => unwrap(api.POST("/api/auth/login", { body: { email, password } })),
    onSuccess: (me) => {
      qc.setQueryData(["me"], me);
      const next = params.get("next");
      navigate(next?.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <main className="login">
      <form onSubmit={submit} className="login-form" noValidate={false}>
        <div className="login-brand">
          <BrandMark size="l" />
          <span>Второй ключ</span>
        </div>
        <p className="login-lead">Проверяет закупки из 1С и показывает, где компания переплатила.</p>
        <div className="login-fields">
          <label className="field">
            <span className="field-label">Email</span>
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="field">
            <span className="field-label">Пароль</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {login.error && (
            <p className="inline-error" role="alert">
              <Icon name="alert" size={18} strokeWidth={2} />
              {login.error.message}
            </p>
          )}
          <button className="btn btn-primary btn-lg" disabled={login.isPending}>
            {login.isPending ? "Входим…" : "Войти"}
          </button>
        </div>
        <p className="login-foot">Вход по приглашению. Нет доступа — попросите собственника компании пригласить вас.</p>
      </form>
    </main>
  );
}
