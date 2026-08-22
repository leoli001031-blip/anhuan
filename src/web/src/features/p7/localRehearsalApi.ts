import { tenantFetch, ApiError } from "../../api";
import { p7ReasonCopy } from "./reasonCopy";
import type {
  CreateRehearsalCheckInput,
  CreateRehearsalPlanInput,
  LocalRehearsalDashboard,
  RecordRehearsalResultInput,
  RehearsalCheck,
  RehearsalPlanCollection,
  RehearsalPlanDetail,
  RehearsalRunDetail,
  UpdateRehearsalCheckInput,
} from "./types";

export const LOCAL_REHEARSAL_BASE = "/v1/local-rehearsal";

export class LocalRehearsalApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(p7ReasonCopy(code));
    this.name = "LocalRehearsalApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

interface RequestOptions {
  token: string | null;
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
}

function mapTransportError(error: unknown): LocalRehearsalApiError {
  if (error instanceof LocalRehearsalApiError) return error;
  if (error instanceof ApiError) {
    if (error.code === "TENANT_SNAPSHOT_UNREADY") {
      return new LocalRehearsalApiError(0, "TENANT_CONTEXT_REQUIRED", false);
    }
    return new LocalRehearsalApiError(error.status, error.code, error.retryable);
  }
  return new LocalRehearsalApiError(0, "NETWORK_ERROR", true);
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  if (!path.startsWith(LOCAL_REHEARSAL_BASE)) throw new LocalRehearsalApiError(0, "INVALID_P7_PATH", false);
  try {
    const result = await tenantFetch<T>(path, {
      method: options.method,
      token: options.token,
      body: options.body,
      signal: options.signal,
      parse: "json",
    });
    return result.payload;
  } catch (error) {
    throw mapTransportError(error);
  }
}

function itemPath(segment: string, id: string): string {
  return LOCAL_REHEARSAL_BASE + segment + "/" + encodeURIComponent(id);
}

export function userFacingLocalRehearsalError(error: unknown): string {
  return error instanceof LocalRehearsalApiError ? p7ReasonCopy(error.code) : p7ReasonCopy("NETWORK_ERROR");
}

export function isLocalRehearsalRequestAborted(error: unknown): boolean {
  return error instanceof LocalRehearsalApiError && error.code === "REQUEST_ABORTED";
}

export function getLocalRehearsalDashboard(token: string | null, signal?: AbortSignal): Promise<LocalRehearsalDashboard> {
  return requestJson<LocalRehearsalDashboard>(LOCAL_REHEARSAL_BASE + "/dashboard", { token, signal });
}

export function listRehearsalPlans(token: string | null, signal?: AbortSignal): Promise<RehearsalPlanCollection> {
  return requestJson<RehearsalPlanCollection>(LOCAL_REHEARSAL_BASE + "/plans", { token, signal });
}

export function createRehearsalPlan(token: string | null, input: CreateRehearsalPlanInput, signal?: AbortSignal): Promise<RehearsalPlanDetail> {
  return requestJson<RehearsalPlanDetail>(LOCAL_REHEARSAL_BASE + "/plans", { token, method: "POST", body: input, signal });
}

export function getRehearsalPlan(token: string | null, planId: string, signal?: AbortSignal): Promise<RehearsalPlanDetail> {
  return requestJson<RehearsalPlanDetail>(itemPath("/plans", planId), { token, signal });
}

export function createRehearsalCheck(token: string | null, planId: string, input: CreateRehearsalCheckInput, signal?: AbortSignal): Promise<RehearsalCheck> {
  return requestJson<RehearsalCheck>(itemPath("/plans", planId) + "/checks", { token, method: "POST", body: input, signal });
}

export function updateRehearsalCheck(token: string | null, checkId: string, input: UpdateRehearsalCheckInput, signal?: AbortSignal): Promise<RehearsalCheck> {
  return requestJson<RehearsalCheck>(itemPath("/checks", checkId), { token, method: "PATCH", body: input, signal });
}

export function createRehearsalRun(token: string | null, planId: string, signal?: AbortSignal): Promise<RehearsalRunDetail> {
  return requestJson<RehearsalRunDetail>(itemPath("/plans", planId) + "/runs", { token, method: "POST", signal });
}

export function getRehearsalRun(token: string | null, runId: string, signal?: AbortSignal): Promise<RehearsalRunDetail> {
  return requestJson<RehearsalRunDetail>(itemPath("/runs", runId), { token, signal });
}

export function recordRehearsalResult(token: string | null, runId: string, resultId: string, input: RecordRehearsalResultInput, signal?: AbortSignal): Promise<RehearsalRunDetail> {
  return requestJson<RehearsalRunDetail>(itemPath("/runs", runId) + "/checks/" + encodeURIComponent(resultId), { token, method: "PATCH", body: input, signal });
}

export function completeRehearsalRun(token: string | null, runId: string, signal?: AbortSignal): Promise<RehearsalRunDetail> {
  return requestJson<RehearsalRunDetail>(itemPath("/runs", runId) + "/complete", { token, method: "POST", signal });
}

export function cancelRehearsalRun(token: string | null, runId: string, signal?: AbortSignal): Promise<RehearsalRunDetail> {
  return requestJson<RehearsalRunDetail>(itemPath("/runs", runId) + "/cancel", { token, method: "POST", signal });
}
