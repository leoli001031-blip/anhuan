import { api, ApiError } from "./api";

export type ServiceCaseAction =
  | "create"
  | "edit"
  | "assign"
  | "plan_visit"
  | "close"
  | string;
export type AssignmentAction = "accept" | "reject" | "revoke" | string;
export type SiteVisitAction =
  | "edit_visit"
  | "start_visit"
  | "complete_visit"
  | string;

export interface ServiceAssignment {
  id: string;
  assignee_user_id: string;
  capacity: string;
  status: string;
  allowed_actions: AssignmentAction[];
}

export interface SiteVisit {
  id: string;
  status: string;
  planned_start_at: string | null;
  planned_end_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  allowed_actions: SiteVisitAction[];
}

export interface SiteVisitInput {
  planned_start_at: string | null;
  planned_end_at: string | null;
}

export interface ServiceCaseFinding {
  id: string;
  title: string;
  severity: string;
  status: string;
  due_at: string | null;
}

export interface BusinessTimelineItem {
  id: string;
  event_type: string;
  occurred_at: string;
  actor_user_id?: string | null;
  subject_type?: string;
  subject_id?: string | null;
}

export interface ServiceCase {
  id: string;
  client_account_id: string | null;
  title: string;
  description: string | null;
  service_type: string;
  status: string;
  planned_start_at: string | null;
  planned_end_at: string | null;
  allowed_actions: ServiceCaseAction[];
  assignments?: ServiceAssignment[];
  site_visits?: SiteVisit[];
  findings?: ServiceCaseFinding[];
  finding_summary?: Record<string, number>;
  timeline?: BusinessTimelineItem[];
}

export interface ServiceCaseInput {
  title: string;
  description: string | null;
  service_type: string;
  planned_start_at: string | null;
  planned_end_at: string | null;
}

export interface ServiceCaseCreateInput extends ServiceCaseInput {
  client_account_id?: string | null;
}

// 客户门户只持有后端 client-safe 摘要，不复用 provider 的详情 DTO。
export interface PortalServiceCaseSummary {
  id: string;
  title: string;
  service_type: string;
  status: string;
  planned_start_at: string | null;
  planned_end_at: string | null;
  assigned: boolean;
  updated_at: string;
}

export interface PortalServiceCaseCollection {
  items: PortalServiceCaseSummary[];
  allowed_actions: [];
}

const PORTAL_SERVICE_CASE_KEYS = new Set([
  "id",
  "title",
  "service_type",
  "status",
  "planned_start_at",
  "planned_end_at",
  "assigned",
  "updated_at",
]);

export interface AssignmentCandidate {
  user_id: string;
  membership_role: string;
  allowed_capacities: string[];
}

export interface ServiceCaseCollection {
  items: ServiceCase[];
  allowed_actions: ServiceCaseAction[];
}

function normalizeCollection(
  payload: ServiceCase[] | ServiceCaseCollection,
): ServiceCaseCollection {
  if (Array.isArray(payload)) {
    const allowedActions = payload.flatMap((item) => item.allowed_actions ?? []);
    return {
      items: payload,
      allowed_actions: Array.from(new Set(allowedActions)),
    };
  }
  return {
    items: payload.items ?? [],
    allowed_actions: payload.allowed_actions ?? [],
  };
}

export async function listServiceCases(
  token: string | null,
  scope: "all" | "mine" = "all",
): Promise<ServiceCaseCollection> {
  const suffix = scope === "mine" ? "/mine" : "";
  const payload = await api<ServiceCase[] | ServiceCaseCollection>(
    `/v1/service-cases${suffix}`,
    { token },
  );
  return normalizeCollection(payload);
}

function serviceCasesContractError(code: string): never {
  // 前端已收到响应但客户归属/合同校验失败，不得冒充网络故障。
  throw new ApiError(409, code, false);
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    serviceCasesContractError("SERVICE_CASES_CONTRACT_INVALID");
  }
  return value as Record<string, unknown>;
}

function requiredString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value.trim() === "") {
    serviceCasesContractError("SERVICE_CASES_CONTRACT_INVALID");
  }
  return value;
}

function nullableString(row: Record<string, unknown>, key: string): string | null {
  const value = row[key];
  if (value === null) return null;
  if (typeof value !== "string" || value.trim() === "") {
    serviceCasesContractError("SERVICE_CASES_CONTRACT_INVALID");
  }
  return value;
}

function parsePortalServiceCase(value: unknown): PortalServiceCaseSummary {
  const row = asRecord(value);
  const keys = Object.keys(row);
  if (
    keys.length !== PORTAL_SERVICE_CASE_KEYS.size ||
    keys.some((key) => !PORTAL_SERVICE_CASE_KEYS.has(key)) ||
    typeof row.assigned !== "boolean"
  ) {
    serviceCasesContractError("SERVICE_CASES_CONTRACT_INVALID");
  }
  return {
    id: requiredString(row, "id"),
    title: requiredString(row, "title"),
    service_type: requiredString(row, "service_type"),
    status: requiredString(row, "status"),
    planned_start_at: nullableString(row, "planned_start_at"),
    planned_end_at: nullableString(row, "planned_end_at"),
    assigned: row.assigned,
    updated_at: requiredString(row, "updated_at"),
  };
}

export async function listPortalServiceCases(
  token: string | null,
): Promise<PortalServiceCaseCollection> {
  const payload = await api<unknown>("/v1/service-cases/portal", { token });
  const row = asRecord(payload);
  if (!Array.isArray(row.items) || !Array.isArray(row.allowed_actions)) {
    serviceCasesContractError("SERVICE_CASES_CONTRACT_INVALID");
  }
  if (row.allowed_actions.length !== 0) {
    serviceCasesContractError("SERVICE_CASES_PORTAL_ACTIONS_FORBIDDEN");
  }
  return {
    items: row.items.map(parsePortalServiceCase),
    allowed_actions: [],
  };
}

export async function listClientServiceCases(
  token: string | null,
  clientAccountId: string,
): Promise<ServiceCaseCollection> {
  const payload = await api<ServiceCase[] | ServiceCaseCollection>(
    `/v1/service-cases?client_account_id=${encodeURIComponent(clientAccountId)}`,
    { token },
  );
  const collection = normalizeCollection(payload);
  if (collection.items.some((item) => item.client_account_id !== clientAccountId)) {
    serviceCasesContractError("SERVICE_CASE_CLIENT_SCOPE_MISMATCH");
  }
  return collection;
}

export function getServiceCase(
  token: string | null,
  caseId: string,
): Promise<ServiceCase> {
  return api<ServiceCase>(`/v1/service-cases/${caseId}`, { token });
}

// 运营台的客户详情必须二次校验归属，避免从其他客户的路由/缓存带入数据。
export async function getClientServiceCase(
  token: string | null,
  clientAccountId: string,
  caseId: string,
): Promise<ServiceCase> {
  const serviceCase = await getServiceCase(token, caseId);
  if (serviceCase.client_account_id !== clientAccountId) {
    serviceCasesContractError("SERVICE_CASE_CLIENT_SCOPE_MISMATCH");
  }
  return serviceCase;
}

export function createServiceCase(
  token: string | null,
  body: ServiceCaseCreateInput,
): Promise<ServiceCase> {
  return api<ServiceCase>("/v1/service-cases", {
    method: "POST",
    token,
    body,
  });
}

export async function createClientServiceCase(
  token: string | null,
  clientAccountId: string,
  body: ServiceCaseInput,
): Promise<ServiceCase> {
  const created = await createServiceCase(token, {
    ...body,
    client_account_id: clientAccountId,
  });
  if (created.client_account_id !== clientAccountId) {
    serviceCasesContractError("SERVICE_CASE_CLIENT_SCOPE_MISMATCH");
  }
  return created;
}

export function updateServiceCase(
  token: string | null,
  caseId: string,
  body: ServiceCaseInput,
): Promise<ServiceCase> {
  return api<ServiceCase>(`/v1/service-cases/${caseId}`, {
    method: "PATCH",
    token,
    body,
  });
}

export function listAssignmentCandidates(
  token: string | null,
): Promise<AssignmentCandidate[]> {
  return api<AssignmentCandidate[]>(
    "/v1/service-cases/assignment-candidates",
    { token },
  );
}

export function createAssignment(
  token: string | null,
  caseId: string,
  body: { assignee_user_id: string; capacity: string },
): Promise<ServiceAssignment> {
  return api<ServiceAssignment>(`/v1/service-cases/${caseId}/assignments`, {
    method: "POST",
    token,
    body,
  });
}

export function actOnAssignment(
  token: string | null,
  caseId: string,
  assignmentId: string,
  action: "accept" | "reject" | "revoke",
): Promise<ServiceAssignment> {
  return api<ServiceAssignment>(
    `/v1/service-cases/${caseId}/assignments/${assignmentId}/${action}`,
    { method: "POST", token },
  );
}

export function createSiteVisit(
  token: string | null,
  caseId: string,
  body: SiteVisitInput,
): Promise<SiteVisit> {
  return api<SiteVisit>(`/v1/service-cases/${caseId}/site-visits`, {
    method: "POST",
    token,
    body,
  });
}

export function updateSiteVisit(
  token: string | null,
  caseId: string,
  visitId: string,
  body: SiteVisitInput,
): Promise<SiteVisit> {
  return api<SiteVisit>(
    `/v1/service-cases/${caseId}/site-visits/${visitId}`,
    { method: "PATCH", token, body },
  );
}

export function actOnSiteVisit(
  token: string | null,
  caseId: string,
  visitId: string,
  action: "start" | "complete",
): Promise<SiteVisit> {
  return api<SiteVisit>(
    `/v1/service-cases/${caseId}/site-visits/${visitId}/${action}`,
    { method: "POST", token },
  );
}

export function closeServiceCase(
  token: string | null,
  caseId: string,
): Promise<ServiceCase> {
  return api<ServiceCase>(`/v1/service-cases/${caseId}/close`, {
    method: "POST",
    token,
  });
}
