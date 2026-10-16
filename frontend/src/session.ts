import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { api, unwrap, type Finding, type Role, type Run } from "./api/client";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => unwrap(api.GET("/api/auth/me")), retry: false });
}

/** Роль в текущей организации (первая — до появления переключателя организаций). */
export function useRole(): Role | undefined {
  return useMe().data?.memberships[0]?.role;
}

export const canDecide = (r?: Role) => r === "owner" || r === "finance";
export const canUpload = (r?: Role) => r === "owner" || r === "finance" || r === "accountant";
export const canSeeAll = (r?: Role) => r !== undefined && r !== "buyer";
export const canAnswer = (r?: Role) => r !== undefined && r !== "viewer";
/** Связки и досье поставщиков — собственнику и финансам (следы о людях и поставщиках). */
export const canSeeLinks = canDecide;

export const ROLE_TEXT: Record<Role, string> = {
  owner: "Собственник",
  finance: "Финансы",
  accountant: "Бухгалтер",
  buyer: "Снабжение",
  viewer: "Наблюдатель",
};

// --- проверка (audit run), с которой сейчас работает человек ------------------------------------

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/audits", { params: { query: { limit: 50 } } })),
    // идущая проверка обновляется, пока не завершится
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status !== "done" && r.status !== "failed") ? 3000 : false),
  });
}

type RunCtx = { run: Run | undefined; runs: Run[]; select: (id: string) => void; loading: boolean };
export const RunContext = createContext<RunCtx>({ run: undefined, runs: [], select: () => {}, loading: true });
/** Выбранная проверка; по умолчанию — последняя завершённая. */
export const useCurrentRun = () => useContext(RunContext);

export function pickRun(runs: Run[], selected: string | null): Run | undefined {
  return runs.find((r) => r.id === selected) ?? runs.find((r) => r.status === "done");
}

/** Все вопросы проверки одним запросом: их десятки, фильтры и счётчики — на клиенте. */
export function useRunFindings(runId: string | undefined) {
  return useQuery({
    queryKey: ["findings", runId],
    enabled: !!runId,
    queryFn: () => unwrap(api.GET("/api/findings", { params: { query: { run_id: runId, limit: 200 } } })),
    select: (d) => d.items,
  });
}

export function useSummary(runId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["summary", runId],
    enabled: !!runId && enabled,
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}/summary", { params: { path: { run_id: runId! } } })),
  });
}

export function useLinks(runId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["links", runId],
    enabled: !!runId && enabled,
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}/links", { params: { path: { run_id: runId! } } })),
  });
}

/** Что настроено на сервере: пароли 1С (SK_DATA_KEY), ИИ, агрегатор реестров. */
export function useCapabilities(enabled = true) {
  return useQuery({ queryKey: ["capabilities"], enabled, queryFn: () => unwrap(api.GET("/api/integrations/capabilities")) });
}

/** Поставщики проверки (досье без позиций): найти карточку по имени из вопроса. */
export function useRunSuppliers(runId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["suppliers", runId],
    enabled: !!runId && enabled,
    queryFn: () => unwrap(api.GET("/api/audits/{run_id}/suppliers", { params: { path: { run_id: runId! } } })),
  });
}

/** Очередь разбора: ждущие решения, крупные сверху (сервер уже отсортировал по сумме). */
export const waitingQueue = (items: Finding[] | undefined) =>
  (items ?? []).filter((f) => f.status === "open" || f.status === "answered");
