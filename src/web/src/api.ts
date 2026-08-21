// Unified API client: relative /api, tenant header, bearer token.
// The vite dev proxy forwards /api -> 8001 and /realms -> 8080, so the
// frontend never hardcodes a host/port.

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

const ENTERPRISE_KEY = "f1-selected-enterprise";
export const ENTERPRISE_CHANGED_EVENT = "f1-enterprise-changed";
let tenantRequestController = new AbortController();
let tenantRequestGeneration = 0;

interface MergedAbortSignal {
  signal: AbortSignal;
  dispose: () => void;
}

function mergeAbortSignals(...signals: Array<AbortSignal | undefined>): MergedAbortSignal {
  const activeSignals = signals.filter((signal): signal is AbortSignal => Boolean(signal));
  if (activeSignals.length === 1) {
    return { signal: activeSignals[0], dispose: () => undefined };
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  for (const signal of activeSignals) {
    if (signal.aborted) {
      controller.abort();
      break;
    }
    signal.addEventListener("abort", abort, { once: true });
  }
  return {
    signal: controller.signal,
    dispose: () => {
      for (const signal of activeSignals) signal.removeEventListener("abort", abort);
    },
  };
}

function assertTenantRequestCurrent(signal: AbortSignal, generation: number): void {
  if (signal.aborted || generation !== tenantRequestGeneration) {
    throw new ApiError(0, "REQUEST_ABORTED", false);
  }
}

export function getSelectedEnterprise(): string | null {
  return localStorage.getItem(ENTERPRISE_KEY);
}

export function setSelectedEnterprise(id: string | null): void {
  const current = getSelectedEnterprise();
  if (current === id) return;
  const previousController = tenantRequestController;
  tenantRequestController = new AbortController();
  tenantRequestGeneration += 1;
  previousController.abort();
  if (id) {
    localStorage.setItem(ENTERPRISE_KEY, id);
  } else {
    localStorage.removeItem(ENTERPRISE_KEY);
  }
  window.dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT));
}

export async function api<T = any>(
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
  if (!path.startsWith("/v1/") && path !== "/v1") {
    throw new ApiError(0, "INVALID_API_PATH", false);
  }
  const headers: Record<string, string> = {};
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  const enterpriseId = options.enterpriseId !== undefined ? options.enterpriseId : getSelectedEnterprise();
  if (enterpriseId) {
    headers["X-Enterprise-Id"] = enterpriseId;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const requestGeneration = tenantRequestGeneration;
  const mergedSignal = mergeAbortSignals(options.signal, tenantRequestController.signal);
  try {
    let resp: Response;
    try {
      resp = await fetch(`${API}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        signal: mergedSignal.signal,
      });
    } catch (error) {
      if (
        mergedSignal.signal.aborted ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        throw new ApiError(0, "REQUEST_ABORTED", false);
      }
      throw new ApiError(0, "NETWORK_ERROR", true);
    }
    assertTenantRequestCurrent(mergedSignal.signal, requestGeneration);
    if (!resp.ok) {
      const failure = await responseError(resp);
      assertTenantRequestCurrent(mergedSignal.signal, requestGeneration);
      throw failure;
    }
    if (resp.status === 204) {
      assertTenantRequestCurrent(mergedSignal.signal, requestGeneration);
      return undefined as T;
    }
    const payload = (await resp.json()) as T;
    assertTenantRequestCurrent(mergedSignal.signal, requestGeneration);
    return payload;
  } finally {
    mergedSignal.dispose();
  }
}
