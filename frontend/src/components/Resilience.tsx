// Устойчивость интерфейса: полоса «нет интернета» и перехват ошибок отрисовки вместо белого экрана.
import { Component, useSyncExternalStore, type ErrorInfo, type ReactNode } from "react";
import { Icon } from "./Icon";

const subscribe = (cb: () => void) => {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
};

export function useOnline() {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}

export function OfflineBar() {
  const online = useOnline();
  if (online) return null;
  return (
    <div className="offline-bar" role="status">
      <Icon name="alert" size={16} strokeWidth={2} />
      Нет интернета. Показываем последние загруженные данные; решения и ответы сохранятся, когда связь вернётся — нажмите ещё раз.
    </div>
  );
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("render error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="empty crash" role="alert">
        <p className="empty-title">Экран не открылся</p>
        <div className="empty-text">
          Данные не потеряны. Обновите страницу; если повторится — напишите нам и приложите время ошибки:{" "}
          {new Date().toLocaleString("ru-RU")}.
        </div>
        <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
          Обновить страницу
        </button>
      </div>
    );
  }
}
