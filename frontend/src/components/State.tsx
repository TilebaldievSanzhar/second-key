import type { ReactNode } from "react";

// Пустые, загрузочные и ошибочные состояния — часть интерфейса, а не заглушки
export function Loading({ label = "Загружаем…" }: { label?: string }) {
  return <p className="muted" role="status">{label}</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  const text = error instanceof Error ? error.message : "Не удалось загрузить данные";
  return <p className="error" role="alert">{text}</p>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children}
    </div>
  );
}
