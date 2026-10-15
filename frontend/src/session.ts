import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Role } from "./api/client";

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
