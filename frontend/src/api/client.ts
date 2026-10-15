// Типизированный клиент API: пути и схемы — из OpenAPI бэкенда (npm run api-types).
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Run = Schemas["RunOut"];
export type RunSummary = Schemas["RunSummary"];
export type Finding = Schemas["FindingOut"];
export type FindingDetail = Schemas["FindingDetail"];
export type FindingStatus = Schemas["FindingStatus"];
export type Upload = Schemas["UploadOut"];
export type Me = Schemas["MeOut"];
export type Org = Schemas["OrgOut"];
export type PriceHistory = Schemas["PriceHistory"];
export type Role = Schemas["Role"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// сессия истекла — на вход, вернувшись потом туда же
const onUnauthorized: Middleware = {
  onResponse({ response, request }) {
    if (response.status === 401 && !request.url.includes("/api/auth/")) {
      const back = encodeURIComponent(location.pathname + location.search);
      location.assign(`/login?next=${back}`);
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use(onUnauthorized);

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Данные или ApiError с текстом бэкенда (detail) — его можно показывать пользователю. */
export async function unwrap<T>(p: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || data === undefined) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Проверьте введённые значения"
          : `Сервер ответил ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return data;
}
