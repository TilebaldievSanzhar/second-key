// Тема: «как в системе» (по умолчанию), светлая или тёмная. Выбор — удобство этого браузера (localStorage),
// поэтому все обращения к хранилищу в try/catch: в приватном окне его может не быть — тогда тема живёт до перезагрузки.
// До первой отрисовки тему ставит public/theme.js, чтобы страница не мигала светлой.
import { useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";
const KEY = "sk-theme";
const EVENT = "sk-theme";
const BAR = { light: "#f6f5f1", dark: "#121413" };

function stored(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

let current: Theme = stored();

// цвет панели браузера: при явном выборе — обе meta на одну тему, при системной — каждая своему media
function apply(theme: Theme) {
  current = theme;
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
    const own = m.getAttribute("media")?.includes("dark") ? "dark" : "light";
    m.setAttribute("content", BAR[theme === "system" ? own : theme]);
  });
  window.dispatchEvent(new Event(EVENT));
}

export function setTheme(theme: Theme) {
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    /* не сохранилось — применяем только в этой вкладке */
  }
  apply(theme);
}

const subscribe = (cb: () => void) => {
  // выбор в другой вкладке того же браузера
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) apply(stored());
  };
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", onStorage);
  };
};

export function useTheme(): [Theme, (t: Theme) => void] {
  return [useSyncExternalStore(subscribe, () => current, () => "system" as Theme), setTheme];
}
