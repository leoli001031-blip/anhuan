import { api } from "./api";

export type FindingScope = "all" | "rectification" | "review";
export type FindingAction =
  | "create"
  | "edit"
  | "start_rectification"
  | "submit_correction"
  | "start_review"
  | "pass"
  | "reject"
  | "close"
  | string;

export interface CorrectiveAction {
  id: string;
  revision?: number;
  description: string;
  submitted_by_user_id?: string;
  created_at?: string;
  submitted_at?: string;
}

export interface FindingReview {
  id: string;
  decision: "passed" | "rejected" | string;
  comment: string | null;
  reviewed_by_user_id?: string;
  created_at?: string;
  reviewed_at?: string;
}

export interface Finding {
  id: string;
  service_case_id: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  responsible_user_id: string | null;
  due_at: string;
  created_at?: string;
  updated_at?: string;
  allowed_actions: FindingAction[];
  corrective_actions?: CorrectiveAction[];
  reviews?: FindingReview[];
}

export interface FindingCreateInput {
  service_case_id: string;
  title: string;
  description: string;
  severity: string;
  responsible_user_id: string | null;
  due_at: string;
}

export type FindingUpdateInput = Omit<FindingCreateInput, "service_case_id">;

export interface FindingCollection {
  items: Finding[];
  allowed_actions: FindingAction[];
}

function normalizeCollection(
  payload: Finding[] | FindingCollection,
): FindingCollection {
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

export async function listFindings(
  token: string | null,
  scope: FindingScope,
  caseId?: string | null,
): Promise<FindingCollection> {
  const query = new URLSearchParams({ scope });
  if (caseId) query.set("service_case_id", caseId);
  const payload = await api<Finding[] | FindingCollection>(
    `/v1/findings?${query.toString()}`,
    { token },
  );
  return normalizeCollection(payload);
}

export function createFinding(
  token: string | null,
  body: FindingCreateInput,
): Promise<Finding> {
  return api<Finding>("/v1/findings", { method: "POST", token, body });
}

export function getFinding(
  token: string | null,
  findingId: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}`, { token });
}

export function updateFinding(
  token: string | null,
  findingId: string,
  body: FindingUpdateInput,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}`, {
    method: "PATCH",
    token,
    body,
  });
}

export function startRectification(
  token: string | null,
  findingId: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}/start-rectification`, {
    method: "POST",
    token,
  });
}

export function submitCorrectiveAction(
  token: string | null,
  findingId: string,
  description: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}/corrective-actions`, {
    method: "POST",
    token,
    body: { description },
  });
}

export function startFindingReview(
  token: string | null,
  findingId: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}/start-review`, {
    method: "POST",
    token,
  });
}

export function reviewFinding(
  token: string | null,
  findingId: string,
  decision: "passed" | "rejected",
  comment: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}/reviews`, {
    method: "POST",
    token,
    body: { decision, comment },
  });
}

export function closeFinding(
  token: string | null,
  findingId: string,
): Promise<Finding> {
  return api<Finding>(`/v1/findings/${findingId}/close`, {
    method: "POST",
    token,
  });
}
