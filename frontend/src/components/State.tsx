import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { Icon } from "./Icon";

// Пустые, загрузочные и ошибочные состояния — часть интерфейса, а не заглушки

/** Скелет вместо спиннера: форма будущего содержимого. */
export function Skeleton({ lines = 4, label = "Загружаем…" }: { lines?: number; label?: string }) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      <span className="sk sk-title" />
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="sk" style={{ width: `${92 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const offline = typeof navigator !== "undefined" && !navigator.onLine;
  const text = offline
    ? "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз."
    : error instanceof ApiError
      ? error.message
      : "Сервер не отвечает. Данные не потеряны, попробуйте через минуту.";
  return (
    <div className="note note-error" role="alert">
      <Icon name="alert" size={18} strokeWidth={2} />
      <span>
        {text}
        {error instanceof ApiError && error.requestId && <span className="note-id"> Запрос {error.requestId.slice(0, 8)}</span>}
      </span>
      {retry && (
        <button type="button" className="btn btn-link" onClick={retry}>
          Повторить
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-text">{children}</div>}
      {action}
    </div>
  );
}

export function Notice({ tone = "over", children }: { tone?: "over" | "accent"; children: ReactNode }) {
  return (
    <div className={`note note-${tone}`}>
      {tone === "over" && <Icon name="alert" size={18} strokeWidth={2} />}
      <span>{children}</span>
    </div>
  );
}
