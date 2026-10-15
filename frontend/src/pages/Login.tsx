import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";

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
      navigate(next?.startsWith("/") ? next : "/", { replace: true });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <main className="login">
      <form onSubmit={submit} className="login-card">
        <h1>Второй ключ</h1>
        <p className="muted">Независимая проверка закупок для собственника</p>
        <label>
          Email
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Пароль
          <input type="password" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {login.error && <p className="error" role="alert">{login.error.message}</p>}
        <button className="primary" disabled={login.isPending}>{login.isPending ? "Входим…" : "Войти"}</button>
      </form>
    </main>
  );
}
