import { tenantFetch, ApiError } from "../../api";
import { p6ReasonCopy } from "./reasonCopy";
import type {
  CreateQualityScenarioInput,
  CreateQualitySuiteInput,
  QualityDashboard,
  QualityDisagreement,
  QualityDisagreementCollection,
  QualityRunDetail,
  QualityScenario,
  QualitySuite,
  QualitySuiteCollection,
  QualitySuiteDetail,
  ReviewDisagreementInput,
  UpdateQualityScenarioInput,
} from "./types";

export const P6_AUTOMATED_QUALITY_BASE = "/v1/automated-quality";

export class AutomatedQualityApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(p6ReasonCopy(code));
    this.name = "AutomatedQualityApiError";
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

function assertP6Path(path: string): void {
  if (!path.startsWith(P6_AUTOMATED_QUALITY_BASE)) {
    throw new AutomatedQualityApiError(0, "INVALID_P6_PATH", false);
  }
}

function mapTransportError(error: unknown): AutomatedQualityApiError {
  if (error instanceof AutomatedQualityApiError) return error;
  if (error instanceof ApiError) {
    if (error.code === "TENANT_SNAPSHOT_UNREADY") {
      return new AutomatedQualityApiError(0, "TENANT_CONTEXT_REQUIRED", false);
    }
    return new AutomatedQualityApiError(error.status, error.code, error.retryable);
  }
  return new AutomatedQualityApiError(0, "NETWORK_ERROR", true);
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  assertP6Path(path);
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
  return P6_AUTOMATED_QUALITY_BASE + segment + "/" + encodeURIComponent(id);
}

export function userFacingAutomatedQualityError(error: unknown): string {
  if (error instanceof AutomatedQualityApiError) return p6ReasonCopy(error.code);
  return p6ReasonCopy("NETWORK_ERROR");
}

export function isAutomatedQualityRequestAborted(error: unknown): boolean {
  return error instanceof AutomatedQualityApiError && error.code === "REQUEST_ABORTED";
}

export function getQualityDashboard(
  token: string | null,
  signal?: AbortSignal,
): Promise<QualityDashboard> {
  return requestJson<QualityDashboard>(P6_AUTOMATED_QUALITY_BASE + "/dashboard", { token, signal });
}

export function listQualitySuites(
  token: string | null,
  signal?: AbortSignal,
): Promise<QualitySuiteCollection> {
  return requestJson<QualitySuiteCollection>(P6_AUTOMATED_QUALITY_BASE + "/suites", { token, signal });
}

export function createQualitySuite(
  token: string | null,
  input: CreateQualitySuiteInput,
  signal?: AbortSignal,
): Promise<QualitySuite> {
  return requestJson<QualitySuite>(P6_AUTOMATED_QUALITY_BASE + "/suites", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function getQualitySuite(
  token: string | null,
  suiteId: string,
  signal?: AbortSignal,
): Promise<QualitySuiteDetail> {
  return requestJson<QualitySuiteDetail>(itemPath("/suites", suiteId), { token, signal });
}

export function createQualityScenario(
  token: string | null,
  suiteId: string,
  input: CreateQualityScenarioInput,
  signal?: AbortSignal,
): Promise<QualityScenario> {
  return requestJson<QualityScenario>(itemPath("/suites", suiteId) + "/scenarios", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function updateQualityScenario(
  token: string | null,
  scenarioId: string,
  input: UpdateQualityScenarioInput,
  signal?: AbortSignal,
): Promise<QualityScenario> {
  return requestJson<QualityScenario>(itemPath("/scenarios", scenarioId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}

export function createQualityRun(
  token: string | null,
  suiteId: string,
  signal?: AbortSignal,
): Promise<QualityRunDetail> {
  return requestJson<QualityRunDetail>(itemPath("/suites", suiteId) + "/runs", {
    token,
    method: "POST",
    signal,
  });
}

export function getQualityRun(
  token: string | null,
  runId: string,
  signal?: AbortSignal,
): Promise<QualityRunDetail> {
  return requestJson<QualityRunDetail>(itemPath("/runs", runId), { token, signal });
}

export function listQualityDisagreements(
  token: string | null,
  signal?: AbortSignal,
): Promise<QualityDisagreementCollection> {
  return requestJson<QualityDisagreementCollection>(P6_AUTOMATED_QUALITY_BASE + "/disagreements", { token, signal });
}

export function reviewQualityDisagreement(
  token: string | null,
  disagreementId: string,
  input: ReviewDisagreementInput,
  signal?: AbortSignal,
): Promise<QualityDisagreement> {
  return requestJson<QualityDisagreement>(itemPath("/disagreements", disagreementId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}
