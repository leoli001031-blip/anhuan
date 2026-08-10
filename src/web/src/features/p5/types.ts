export type PolicySourceType = "law" | "regulation" | "standard" | "guidance" | "internal";
export type PolicySourceStatus = "active" | "archived";
export type PolicyDomain = "safety" | "health" | "environment" | "fire" | "chemical" | "general";
export type PolicyEffectStatus = "unknown" | "not_effective" | "effective" | "expired";
export type PolicyWorkflowStatus = "draft" | "in_review" | "approved" | "rejected" | "published" | "superseded";
export type PolicyReviewAction = "submitted" | "approved" | "rejected" | "published";
export type ImpactPriority = "low" | "medium" | "high" | "critical";
export type ImpactStatus = "open" | "accepted" | "dismissed";
export type ImpactTaskStatus = "open" | "in_progress" | "completed" | "dismissed";

export interface PolicySource {
  id: string;
  enterprise_id: string;
  title: string;
  publisher: string;
  source_type: PolicySourceType;
  jurisdiction: string;
  source_reference: string;
  status: PolicySourceStatus;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface PolicyReviewEvent {
  id: string;
  enterprise_id: string;
  policy_version_id: string;
  action: PolicyReviewAction;
  comment: string | null;
  actor_user_id: string;
  occurred_at: string;
  allowed_actions?: string[];
}

export interface PolicyVersion {
  id: string;
  enterprise_id: string;
  source_id: string;
  version_number: number;
  title: string;
  domain: PolicyDomain;
  effect_status: PolicyEffectStatus;
  issued_on: string | null;
  effective_from: string | null;
  effective_to: string | null;
  summary: string;
  document_version_id: string | null;
  document_sha256: string | null;
  workflow_status: PolicyWorkflowStatus;
  created_by_user_id: string;
  submitted_by_user_id?: string | null;
  submitted_at?: string | null;
  approved_by_user_id?: string | null;
  approved_at?: string | null;
  published_by_user_id?: string | null;
  published_at?: string | null;
  created_at: string;
  updated_at?: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface PolicySourceDetail extends PolicySource {
  versions: PolicyVersion[];
}

export interface PolicyVersionDetail extends PolicyVersion {
  source?: PolicySource;
  review_events: PolicyReviewEvent[];
}

export interface PolicySourceCollection {
  items: PolicySource[];
  allowed_actions: string[];
  boundaries?: string[];
}

export interface PolicySearchResult {
  id: string;
  source_id: string;
  version_number: number;
  title: string;
  domain: PolicyDomain;
  effect_status: PolicyEffectStatus;
  issued_on: string | null;
  effective_from: string | null;
  effective_to: string | null;
  summary: string;
  workflow_status: PolicyWorkflowStatus;
  source_title: string;
  publisher: string;
  source_type: PolicySourceType;
  jurisdiction: string;
  source_reference: string;
  allowed_actions: string[];
}

export interface PolicySearchCollection {
  items: PolicySearchResult[];
  count: number;
  boundaries?: string[];
}

export interface CreatePolicySourceInput {
  title: string;
  publisher: string;
  source_type: PolicySourceType;
  jurisdiction: string;
  source_reference: string;
}

export type UpdatePolicySourceInput = Partial<CreatePolicySourceInput> & {
  status?: PolicySourceStatus;
};

export interface CreatePolicyVersionInput {
  title: string;
  domain: PolicyDomain;
  effect_status: PolicyEffectStatus;
  issued_on?: string | null;
  effective_from?: string | null;
  effective_to?: string | null;
  summary: string;
  document_version_id?: string | null;
}

export interface PolicyReviewInput {
  comment?: string | null;
}

export interface PolicyImpactTask {
  id: string;
  enterprise_id: string;
  impact_candidate_id: string;
  title: string;
  owner_user_id: string;
  due_at: string | null;
  status: ImpactTaskStatus;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
}

export interface PolicyImpact {
  id: string;
  enterprise_id: string;
  policy_version_id: string;
  domain: PolicyDomain;
  scope_note: string;
  priority: ImpactPriority;
  status: ImpactStatus;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
  version_title?: string;
  source_title?: string;
}

export interface PolicyImpactDetail extends PolicyImpact {
  tasks: PolicyImpactTask[];
}

export interface PolicyImpactCollection {
  items: PolicyImpact[];
  allowed_actions: string[];
  boundaries?: string[];
}

export interface CreatePolicyImpactInput {
  policy_version_id: string;
  domain: PolicyDomain;
  scope_note: string;
  priority: ImpactPriority;
}

export interface UpdatePolicyImpactInput {
  scope_note?: string;
  priority?: ImpactPriority;
  status?: ImpactStatus;
}

export interface CreateImpactTaskInput {
  title: string;
  owner_user_id: string;
  due_at: string;
}

export interface UpdateImpactTaskInput {
  title?: string;
  owner_user_id?: string | null;
  due_at?: string | null;
  status?: ImpactTaskStatus;
}

export interface PolicySearchParams {
  q?: string;
  domain?: PolicyDomain;
  effect_status?: PolicyEffectStatus;
  workflow_status?: PolicyWorkflowStatus;
}

export interface P5ErrorEnvelope {
  detail?: string | { code?: string; retryable?: boolean };
}
