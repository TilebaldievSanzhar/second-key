import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { NavLink, Navigate, Outlet, useLocation, useMatch } from "react-router";
import { api } from "../api/client";
import { Icon, type IconName } from "../components/Icon";
import { BrandMark } from "../components/RunPicker";
import { ThemeSwitch } from "../components/ThemeSwitch";
import { ErrorBoundary, OfflineBar } from "../components/Resilience";
import { Skeleton } from "../components/State";
import { ToastProvider } from "../components/Toast";
import { initials } from "../format";
import { ROLE_TEXT, RunContext, canDecide, canSeeAll, canSeeLinks, pickRun, useMe, useRunFindings, useRuns } from "../session";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Сводка", icon: "dashboard", end: true },
  { to: "/findings", label: "Вопросы", icon: "inbox" },
  { to: "/links", label: "Связи", icon: "link" },
  { to: "/uploads", label: "Загрузки", icon: "upload" },
  { to: "/settings", label: "Настройки", icon: "sliders" },
];

export function Layout() {
  const me = useMe();
  const location = useLocation();
  const runs = useRuns();
  const [selected, setSelected] = useState<string | null>(null);
  const items = useMemo(() => runs.data?.items ?? [], [runs.data]);
  const run = pickRun(items, selected);
  const ctx = useMemo(() => ({ run, runs: items, select: setSelected, loading: runs.isPending }), [run, items, runs.isPending]);
  const role = me.data?.memberships[0]?.role;
  const findings = useRunFindings(run?.id);
  const onCard = useMatch("/findings/:id");

  if (me.isPending)
    return (
      <div className="boot">
        <Skeleton lines={3} />
      </div>
    );
  if (me.isError) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;

  const m = me.data.memberships[0];
  const seesAll = canSeeAll(role);
  // снабжение работает только со своими вопросами
  if (!seesAll && !location.pathname.startsWith("/findings")) return <Navigate to="/findings" replace />;

  // на ком вопросы: собственнику — «ждут вашего ключа», снабжению — «ждут вашего ответа»
  const list = findings.data ?? [];
  const badge = canDecide(role)
    ? list.filter((f) => f.status === "answered").length
    : role === "buyer"
      ? list.filter((f) => f.status === "open").length
      : 0;
  // «Связи» — собственнику и финансам: следы о поставщиках и сотрудниках
  const nav = seesAll ? NAV.filter((n) => n.to !== "/links" || canSeeLinks(role)) : [{ ...NAV[1]!, label: "Мои вопросы" }];

  return (
    <RunContext.Provider value={ctx}>
      <ToastProvider>
        <div className={`shell${seesAll ? "" : " shell-solo"}${onCard ? " shell-card" : ""}`}>
          <aside className="sidebar">
            <div className="brand">
              <BrandMark />
              <span>Второй ключ</span>
            </div>
            <nav className="side-nav" aria-label="Разделы">
              {nav.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className="side-link">
                  <Icon name={n.icon} size={18} />
                  {n.label}
                  {n.icon === "inbox" && badge > 0 && <span className="side-count">{badge}</span>}
                </NavLink>
              ))}
            </nav>
            <UserBlock name={me.data.user.full_name} role={m ? ROLE_TEXT[m.role] : ""} org={m?.org.name ?? ""} />
          </aside>
          <main className="main" id="main">
            <OfflineBar />
            <ErrorBoundary key={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </main>
          {seesAll && (
            <nav className="bottom-nav" aria-label="Разделы" style={{ gridTemplateColumns: `repeat(${nav.length}, 1fr)` }}>
              {nav.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className="bottom-link">
                  <span className="bottom-icon">
                    <Icon name={n.icon} size={22} />
                    {n.icon === "inbox" && badge > 0 && <span className="bottom-badge">{badge}</span>}
                  </span>
                  {n.label}
                </NavLink>
              ))}
            </nav>
          )}
        </div>
      </ToastProvider>
    </RunContext.Provider>
  );
}

function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.POST("/api/auth/logout"),
    onSettled: () => {
      qc.clear();
      window.location.assign("/login");
    },
  });
}

function UserBlock({ name, role, org }: { name: string; role: string; org: string }) {
  const logout = useLogout();
  return (
    <div className="side-user">
      <span className="side-user-name">{name}</span>
      <span className="side-user-role">
        {role} · {org}
      </span>
      <ThemeSwitch />
      <button type="button" className="btn btn-link side-logout" onClick={() => logout.mutate()}>
        <Icon name="logout" size={16} />
        Выйти
      </button>
    </div>
  );
}

/** Шапка страницы на телефоне: слева заголовок или выбор проверки, справа — инициалы с меню. */
export function MobileHead({ children }: { children?: ReactNode }) {
  const me = useMe();
  const logout = useLogout();
  const m = me.data?.memberships[0];
  if (!me.data) return null;
  return (
    <div className="mhead">
      <div className="mhead-main">{children}</div>
      <details className="usermenu">
        <summary aria-label="Профиль">{initials(me.data.user.full_name)}</summary>
        <div className="usermenu-pop">
          <span className="usermenu-name">{me.data.user.full_name}</span>
          <span className="muted small">
            {m ? ROLE_TEXT[m.role] : ""} · {m?.org.name}
          </span>
          <span className="muted small">Тема</span>
          <ThemeSwitch />
          <button type="button" className="btn btn-secondary" onClick={() => logout.mutate()}>
            Выйти
          </button>
        </div>
      </details>
    </div>
  );
}
