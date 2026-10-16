// Типизированный клиент API: пути и схемы — из OpenAPI бэкенда (npm run api-types).
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Run = Schemas["RunOut"];
export type RunSummary = Schemas["RunSummary"];
export type Breakdown = Schemas["BreakdownRow"];
export type Finding = Schemas["FindingOut"];
export type FindingDetail = Schemas["FindingDetail"];
export type FindingLine = Schemas["FindingLineOut"];
export type FindingStatus = Schemas["FindingStatus"];
export type Upload = Schemas["UploadOut"];
export type Me = Schemas["MeOut"];
export type Org = Schemas["OrgOut"];
export type Member = Schemas["MemberOut"];
export type PriceHistory = Schemas["PriceHistory"];
export type PricePoint = Schemas["PricePoint"];
export type Role = Schemas["Role"];
export type LinksOverview = Schemas["LinksOverview"];
export type LinkItem = Schemas["LinkOut"];
export type MatrixCell = Schemas["MatrixCell"];
export type Trace = Schemas["SupplierTrace"];
export type Supplier = Schemas["SupplierOut"];
export type SupplierDetail = Schemas["SupplierDetail"];
export type Alternatives = Schemas["AlternativesOut"];
export type AltGroup = Schemas["AltGroup"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public requestId?: string,
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

const FALLBACK: Record<number, string> = {
  403: "Это действие доступно собственнику и финансам",
  404: "Не нашли — возможно, у вас нет к этому доступа",
  413: "Файл слишком большой",
};

/** Данные или ApiError с текстом бэкенда (detail) — его можно показывать пользователю. */
export async function unwrap<T>(p: Promise<Result<T>>): Promise<T> {
  let res: Result<T>;
  try {
    res = await p;
  } catch {
    throw new ApiError(0, "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.");
  }
  const { data, error, response } = res;
  if (error !== undefined || data === undefined) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Проверьте введённые значения"
          : response.status >= 500
            ? "Сервер не отвечает. Данные не потеряны, попробуйте через минуту."
            : (FALLBACK[response.status] ?? `Сервер ответил ${response.status}`);
    throw new ApiError(response.status, message, response.headers.get("x-request-id") ?? undefined);
  }
  return data;
}
