// Unified API client: relative /api, tenant header, bearer token.
// The vite dev proxy forwards /api -> 8001 and /realms -> 8080, so the
// frontend never hardcodes a host/port.
// 唯一可赋值 X-Enterprise-Id 的中央 transport 是 tenantFetch。

export const API = "/api";

export interface Membership {
  enterprise_id: string;
  name: string;
  role: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

export {
  ENTERPRISE_CHANGED_EVENT,
  ENTERPRISE_KEY,
  assertTenantBound,
  bindTenantRequest,
  commitTenantSnapshot,
  getSelectedEnterprise,
  getTenantGeneration,
  getTenantSnapshot,
  invalidateTenantContext,
  setSelectedEnterprise,
  type TenantBoundRequest,
} from "./tenantState.ts";

import {
  assertTenantBound,
  bindTenantRequest,
  isTenantAbortError as isTransportAbort,
  TenantTransportError,
} from "./tenantState.ts";

interface ErrorDetail {
  code?: unknown;
  retryable?: unknown;
}

function safeReasonCode(value: unknown, status: number): string {
  if (typeof value === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(value)) {
    return value;
  }
  return status > 0 ? `HTTP_${status}` : "NETWORK_ERROR";
}

async function responseError(response: Response): Promise<ApiError> {
  let detail: unknown;
  if ((response.headers.get("content-type") ?? "").toLowerCase().includes("application/json")) {
    try {
      const payload = (await response.json()) as { detail?: unknown };
      detail = payload?.detail;
    } catch {
      detail = undefined;
    }
  }
  const structured = detail && typeof detail === "object" ? (detail as ErrorDetail) : undefined;
  const code = safeReasonCode(structured?.code ?? detail, response.status);
  const retryable =
    typeof structured?.retryable === "boolean"
      ? structured.retryable
      : response.status === 429 || response.status >= 500;
  return new ApiError(response.status, code, retryable);
}

export function isTenantAbortError(error: unknown): boolean {
  return (
    isTransportAbort(error) ||
    (error instanceof ApiError &&
      (error.code === "REQUEST_ABORTED" || error.code === "TENANT_SNAPSHOT_UNREADY"))
  );
}

function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof TenantTransportError) {
    return new ApiError(error.status, error.code, error.retryable);
  }
  if (isTenantAbortError(error)) {
    return new ApiError(0, "REQUEST_ABORTED", false);
  }
  return new ApiError(0, "NETWORK_ERROR", true);
}

const MEMBERSHIP_PATH = "/v1/users/me/enterprises";

export type TenantFetchParse = "json" | "response" | "void";

export interface TenantFetchOptions {
  method?: string;
  token?: string | null;
  body?: unknown;
  form?: FormData;
  rawBody?: BodyInit;
  contentType?: string;
  extraHeaders?: Record<string, string>;
  signal?: AbortSignal;
  membershipDiscovery?: boolean;
  parse?: TenantFetchParse;
}

export interface TenantFetchResult<T> {
  status: number;
  payload: T;
  enterpriseId: string | null;
  response?: Response;
}

function forbidBypassHeaders(extraHeaders: Record<string, string> | undefined): void {
  for (const key of Object.keys(extraHeaders ?? {})) {
    if (key.toLowerCase() === "x-enterprise-id") {
      throw new ApiError(0, "ENTERPRISE_HEADER_BYPASS_FORBIDDEN", false);
    }
  }
}

export async function tenantFetch<T = unknown>(
  path: string,
  options: TenantFetchOptions = {},
): Promise<TenantFetchResult<T>> {
  if (!path.startsWith("/v1/") && path !== "/v1") {
    throw new ApiError(0, "INVALID_API_PATH", false);
  }
  forbidBypassHeaders(options.extraHeaders);
  const membershipDiscovery = options.membershipDiscovery === true || path === MEMBERSHIP_PATH;
  let bound;
  try {
    bound = bindTenantRequest(
      membershipDiscovery
        ? { enterpriseId: null, signal: options.signal }
        : { signal: options.signal },
    );
  } catch (error) {
    throw asApiError(error);
  }
  const headers: Record<string, string> = { ...(options.extraHeaders ?? {}) };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (bound.enterpriseId) {
    headers["X-Enterprise-Id"] = bound.enterpriseId;
  }
  if (options.body !== undefined && options.form === undefined && options.rawBody === undefined) {
    headers["Content-Type"] = options.contentType ?? "application/json";
  } else if (options.contentType) {
    headers["Content-Type"] = options.contentType;
  }
  const parse = options.parse ?? "json";
  try {
    let resp: Response;
    try {
      resp = await fetch(`${API}${path}`, {
        method: options.method ?? "GET",
        headers,
        body:
          options.form ??
          options.rawBody ??
          (options.body !== undefined ? JSON.stringify(options.body) : undefined),
        signal: bound.signal,
      });
    } catch (error) {
      if (bound.signal.aborted || isTenantAbortError(error)) {
        throw new ApiError(0, "REQUEST_ABORTED", false);
      }
      throw asApiError(error);
    }
    assertTenantBound(bound);
    if (parse === "response") {
      assertTenantBound(bound);
      return {
        status: resp.status,
        payload: undefined as T,
        enterpriseId: bound.enterpriseId,
        response: resp,
      };
    }
    if (!resp.ok) {
      const failure = await responseError(resp);
      assertTenantBound(bound);
      throw failure;
    }
    if (resp.status === 204 || parse === "void") {
      assertTenantBound(bound);
      return { status: resp.status, payload: undefined as T, enterpriseId: bound.enterpriseId };
    }
    const payload = (await resp.json()) as T;
    assertTenantBound(bound);
    return { status: resp.status, payload, enterpriseId: bound.enterpriseId };
  } catch (error) {
    throw asApiError(error);
  } finally {
    bound.dispose();
  }
}

export async function api<T = unknown>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    token?: string | null;
    enterpriseId?: string | null;
    signal?: AbortSignal;
  } = {
    method: "GET",
    token: "",
  },
): Promise<T> {
  const result = await tenantFetch<T>(path, {
    method: options.method,
    token: options.token,
    body: options.body,
    signal: options.signal,
    membershipDiscovery: options.enterpriseId === null || path === MEMBERSHIP_PATH,
    parse: "json",
  });
  return result.payload;
}
