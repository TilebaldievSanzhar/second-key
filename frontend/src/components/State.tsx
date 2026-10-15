import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { Icon } from "./Icon";

// Пустые, загрузочные и ошибочные состояния — часть интерфейса, а не заглушки

/** Скелет вместо спиннера: форма будущего содержимого. variant — по экрану. */
export function Skeleton({
  lines = 4,
  label = "Загружаем…",
  variant = "text",
}: {
  lines?: number;
  label?: string;
  variant?: "text" | "overview" | "list" | "card";
}) {
  if (variant === "overview")
    return (
      <div className="skeleton" role="status" aria-label={label}>
        <span className="sk sk-meta" />
        <div className="sk-hero">
          <span className="sk sk-label" />
          <span className="sk sk-hero-num" />
          <span className="sk sk-meta" />
          <span className="sk sk-btn" />
        </div>
        <SkRows n={3} />
      </div>
    );
  if (variant === "list")
    return (
      <div className="skeleton" role="status" aria-label={label}>
        <SkRows n={lines} />
      </div>
    );
  if (variant === "card")
    return (
      <div className="skeleton" role="status" aria-label={label}>
        <span className="sk sk-meta" />
        <span className="sk sk-title" />
        <span className="sk sk-title" style={{ width: "60%" }} />
        <span className="sk sk-hero-num" style={{ width: "40%" }} />
        <span className="sk sk-chart" />
        <SkRows n={3} />
      </div>
    );
  return (
    <div className="skeleton" role="status" aria-label={label}>
      <span className="sk sk-title" />
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="sk" style={{ width: `${92 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

function SkRows({ n }: { n: number }) {
  return (
    <div className="sk-rows">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="sk-row">
          <span className="sk" style={{ width: `${80 - ((i * 13) % 30)}%` }} />
          <span className="sk sk-amt" />
          <span className="sk sk-meta" style={{ width: `${50 - ((i * 7) % 20)}%` }} />
        </div>
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
