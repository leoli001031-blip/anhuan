import { api } from "./api";

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

export function getServiceCase(
  token: string | null,
  caseId: string,
): Promise<ServiceCase> {
  return api<ServiceCase>(`/v1/service-cases/${caseId}`, { token });
}

export function createServiceCase(
  token: string | null,
  body: ServiceCaseInput,
): Promise<ServiceCase> {
  return api<ServiceCase>("/v1/service-cases", {
    method: "POST",
    token,
    body,
  });
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
