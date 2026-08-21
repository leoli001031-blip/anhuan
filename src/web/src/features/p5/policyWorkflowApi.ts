import { API, getSelectedEnterprise } from "../../api";
import { p5ReasonCopy } from "./reasonCopy";
import type {
  CreateImpactTaskInput,
  CreatePolicyImpactInput,
  CreatePolicySourceInput,
  CreatePolicyVersionInput,
  ConfirmMaterialPolicyDraftInput,
  ConfirmMaterialPolicyDraftResult,
  P5ErrorEnvelope,
  PolicyImpact,
  PolicyImpactCollection,
  PolicyImpactDetail,
  PolicyImpactTask,
  PolicyReviewInput,
  PolicySearchCollection,
  PolicySearchParams,
  PolicySource,
  PolicySourceCollection,
  PolicySourceDetail,
  PolicyVersion,
  PolicyVersionDetail,
  UpdateImpactTaskInput,
  UpdatePolicyImpactInput,
  UpdatePolicySourceInput,
} from "./types";

export const P5_POLICY_WORKFLOW_BASE = "/v1/policy-workflow";

export class PolicyWorkflowApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(p5ReasonCopy(code));
    this.name = "PolicyWorkflowApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

interface RequestOptions {
  token: string | null;
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function assertP5Path(path: string): void {
  if (!path.startsWith(P5_POLICY_WORKFLOW_BASE)) {
    throw new PolicyWorkflowApiError(0, "INVALID_P5_PATH", false);
  }
}

function requestHeaders(options: RequestOptions): Headers {
  const headers = new Headers();
  if (options.token) headers.set("Authorization", "Bearer " + options.token);
  const enterpriseId = getSelectedEnterprise();
  if (enterpriseId) headers.set("X-Enterprise-Id", enterpriseId);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  return headers;
}

function normalizeErrorEnvelope(value: unknown): P5ErrorEnvelope {
  if (!value || typeof value !== "object") return {};
  return value as P5ErrorEnvelope;
}

async function responseError(response: Response): Promise<PolicyWorkflowApiError> {
  let envelope: P5ErrorEnvelope = {};
  try {
    envelope = normalizeErrorEnvelope(await response.json());
  } catch {
    envelope = {};
  }
  const detail = envelope.detail;
  const code =
    typeof detail === "string" && /^[A-Z0-9_]{1,80}$/.test(detail)
      ? detail
      : detail && typeof detail !== "string" && typeof detail.code === "string" && /^[A-Z0-9_]{1,80}$/.test(detail.code)
        ? detail.code
        : response.status === 404
          ? "NOT_FOUND"
          : "HTTP_" + response.status;
  return new PolicyWorkflowApiError(
    response.status,
    code,
    Boolean(detail && typeof detail !== "string" && detail.retryable),
  );
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  assertP5Path(path);
  if (!getSelectedEnterprise()) {
    throw new PolicyWorkflowApiError(0, "TENANT_CONTEXT_REQUIRED", false);
  }
  let response: Response;
  try {
    response = await fetch(API + path, {
      method: options.method ?? "GET",
      headers: requestHeaders(options),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (
      options.signal?.aborted ||
      (error instanceof DOMException && error.name === "AbortError")
    ) {
      throw new PolicyWorkflowApiError(0, "REQUEST_ABORTED", false);
    }
    throw new PolicyWorkflowApiError(0, "NETWORK_ERROR", true);
  }
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new PolicyWorkflowApiError(response.status, "INVALID_RESPONSE", true);
  }
}

function itemPath(segment: string, id: string): string {
  return P5_POLICY_WORKFLOW_BASE + segment + "/" + encodeURIComponent(id);
}

export function userFacingPolicyWorkflowError(error: unknown): string {
  if (error instanceof PolicyWorkflowApiError) return p5ReasonCopy(error.code);
  return p5ReasonCopy("NETWORK_ERROR");
}

export function isPolicyWorkflowRequestAborted(error: unknown): boolean {
  return error instanceof PolicyWorkflowApiError && error.code === "REQUEST_ABORTED";
}

export function listPolicySources(
  token: string | null,
  signal?: AbortSignal,
): Promise<PolicySourceCollection> {
  return requestJson<PolicySourceCollection>(P5_POLICY_WORKFLOW_BASE + "/sources", { token, signal });
}

export function createPolicySource(
  token: string | null,
  input: CreatePolicySourceInput,
  signal?: AbortSignal,
): Promise<PolicySource> {
  return requestJson<PolicySource>(P5_POLICY_WORKFLOW_BASE + "/sources", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function getPolicySource(
  token: string | null,
  sourceId: string,
  signal?: AbortSignal,
): Promise<PolicySourceDetail> {
  return requestJson<PolicySourceDetail>(itemPath("/sources", sourceId), { token, signal });
}

export function updatePolicySource(
  token: string | null,
  sourceId: string,
  input: UpdatePolicySourceInput,
  signal?: AbortSignal,
): Promise<PolicySource> {
  return requestJson<PolicySource>(itemPath("/sources", sourceId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}

export function createPolicyVersion(
  token: string | null,
  sourceId: string,
  input: CreatePolicyVersionInput,
  signal?: AbortSignal,
): Promise<PolicyVersion> {
  return requestJson<PolicyVersion>(itemPath("/sources", sourceId) + "/versions", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function confirmMaterialPolicyDraft(
  token: string | null,
  analysisId: string,
  input: ConfirmMaterialPolicyDraftInput,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ConfirmMaterialPolicyDraftResult> {
  return requestJson<ConfirmMaterialPolicyDraftResult>(
    itemPath("/material-analyses", analysisId) + "/confirm",
    {
      token,
      method: "POST",
      body: input,
      idempotencyKey,
      signal,
    },
  );
}

export function getPolicyVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<PolicyVersionDetail> {
  return requestJson<PolicyVersionDetail>(itemPath("/versions", versionId), { token, signal });
}

export function policyVersionAction(
  token: string | null,
  versionId: string,
  action: "submit" | "approve" | "reject" | "publish",
  input: PolicyReviewInput,
  signal?: AbortSignal,
): Promise<PolicyVersionDetail> {
  return requestJson<PolicyVersionDetail>(itemPath("/versions", versionId) + "/" + action, {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function searchPolicyVersions(
  token: string | null,
  params: PolicySearchParams,
  signal?: AbortSignal,
): Promise<PolicySearchCollection> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.domain) query.set("domain", params.domain);
  if (params.effect_status) query.set("effect_status", params.effect_status);
  if (params.workflow_status) query.set("workflow_status", params.workflow_status);
  const suffix = query.size > 0 ? "?" + query.toString() : "";
  return requestJson<PolicySearchCollection>(P5_POLICY_WORKFLOW_BASE + "/search" + suffix, {
    token,
    signal,
  });
}

export function listPolicyImpacts(
  token: string | null,
  signal?: AbortSignal,
): Promise<PolicyImpactCollection> {
  return requestJson<PolicyImpactCollection>(P5_POLICY_WORKFLOW_BASE + "/impacts", { token, signal });
}

export function createPolicyImpact(
  token: string | null,
  input: CreatePolicyImpactInput,
  signal?: AbortSignal,
): Promise<PolicyImpact> {
  return requestJson<PolicyImpact>(P5_POLICY_WORKFLOW_BASE + "/impacts", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function getPolicyImpact(
  token: string | null,
  impactId: string,
  signal?: AbortSignal,
): Promise<PolicyImpactDetail> {
  return requestJson<PolicyImpactDetail>(itemPath("/impacts", impactId), { token, signal });
}

export function updatePolicyImpact(
  token: string | null,
  impactId: string,
  input: UpdatePolicyImpactInput,
  signal?: AbortSignal,
): Promise<PolicyImpact> {
  return requestJson<PolicyImpact>(itemPath("/impacts", impactId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}

export function createImpactTask(
  token: string | null,
  impactId: string,
  input: CreateImpactTaskInput,
  signal?: AbortSignal,
): Promise<PolicyImpactTask> {
  return requestJson<PolicyImpactTask>(itemPath("/impacts", impactId) + "/tasks", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function updateImpactTask(
  token: string | null,
  taskId: string,
  input: UpdateImpactTaskInput,
  signal?: AbortSignal,
): Promise<PolicyImpactTask> {
  return requestJson<PolicyImpactTask>(itemPath("/impact-tasks", taskId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}
