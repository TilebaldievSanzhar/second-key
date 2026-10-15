// Тосты: результат действия с «Отменить» (6 секунд) вместо диалогов «вы уверены?».
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

type Toast = {
  id: number;
  text: string;
  kind: "done" | "error";
  action?: { label: string; run: () => void };
  ms: number;
};
type Show = (t: Omit<Toast, "id" | "ms" | "kind"> & { kind?: Toast["kind"]; ms?: number }) => void;

const Ctx = createContext<Show>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null);
  const seq = useRef(0);
  const show = useCallback<Show>((t) => {
    seq.current += 1;
    setToast({ kind: "done", ms: 6000, ...t, id: seq.current });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast((cur) => (cur?.id === toast.id ? null : cur)), toast.ms);
    return () => window.clearTimeout(timer);
  }, [toast]);

  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="toast-host" aria-live="polite">
        {toast && (
          <div key={toast.id} className={`toast toast-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
            <div className="toast-body">
              {toast.kind === "error" && <Icon name="alert" size={18} strokeWidth={2} />}
              <span>{toast.text}</span>
              {toast.action && (
                <button
                  type="button"
                  className="toast-action"
                  onClick={() => {
                    toast.action!.run();
                    setToast(null);
                  }}
                >
                  {toast.action.label}
                </button>
              )}
            </div>
            {toast.kind === "done" && (
              <div className="toast-timer">
                <span style={{ animationDuration: `${toast.ms}ms` }} />
              </div>
            )}
          </div>
        )}
      </div>
    </Ctx.Provider>
  );
}
