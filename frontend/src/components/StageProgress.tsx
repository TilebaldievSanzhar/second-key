// Пять стадий проверки: пройденные, текущая, оставшиеся; ошибка — на стадии, где случилась.
import { Icon } from "./Icon";

export const STAGES = [
  { key: "queued", label: "В очереди" },
  { key: "importing", label: "Читаем файл" },
  { key: "normalizing", label: "Сводим номенклатуру" },
  { key: "analyzing", label: "Сравниваем цены" },
  { key: "reporting", label: "Готовим отчёт" },
] as const;

export function StageProgress({ status, progress, failedAt }: { status: string; progress: number; failedAt?: string }) {
  const failed = status === "failed";
  const done = status === "done";
  const currentKey = failed ? (failedAt ?? "importing") : status;
  const idx = done ? STAGES.length : Math.max(0, STAGES.findIndex((s) => s.key === currentKey));
  const title = done ? "Готово" : failed ? "Проверка остановилась" : (STAGES[idx]?.label ?? "В очереди");
  return (
    <div className={`stages${failed ? " stages-failed" : ""}`} aria-live="polite">
      <div className="stages-head">
        <span className="stages-title">{title}</span>
        <span className="num muted">{progress}%</span>
      </div>
      <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
        <span style={{ width: `${progress}%` }} />
      </div>
      <ol className="stage-list">
        {STAGES.map((s, i) => {
          const st = i < idx ? "done" : i === idx && !done ? (failed ? "error" : "now") : "todo";
          return (
            <li key={s.key} className={`stage stage-${st}`}>
              <span className="stage-dot">
                {st === "done" && <Icon name="check" size={12} strokeWidth={3} />}
                {st === "error" && "!"}
              </span>
              {s.label}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
