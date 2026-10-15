import { useMutation, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useLocation } from "react-router";
import { api } from "../api/client";
import { Loading } from "../components/State";
import { canSeeAll, useMe } from "../session";

export function Layout() {
  const me = useMe();
  const location = useLocation();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: () => api.POST("/api/auth/logout"),
    onSuccess: () => {
      qc.clear();
      window.location.assign("/login");
    },
  });

  if (me.isPending) return <Loading />;
  if (me.isError) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  const m = me.data.memberships[0];
  const seesAll = canSeeAll(m?.role);
  // снабжение работает только со своими вопросами
  if (!seesAll && location.pathname !== "/findings" && !location.pathname.startsWith("/findings/"))
    return <Navigate to="/findings" replace />;

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Второй ключ</span>
        <nav>
          {seesAll && <NavLink to="/" end>Сводка</NavLink>}
          <NavLink to="/findings">{seesAll ? "Вопросы" : "Мои вопросы"}</NavLink>
          {seesAll && <NavLink to="/uploads">Загрузки</NavLink>}
          {seesAll && <NavLink to="/settings">Настройки</NavLink>}
        </nav>
        <span className="who">
          {m?.org.name} · {me.data.user.full_name}
          <button className="link" onClick={() => logout.mutate()}>Выйти</button>
        </span>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
