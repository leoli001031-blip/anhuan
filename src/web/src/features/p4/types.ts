export type DashboardView = "admin" | "consultant" | "partner" | "enterprise";

export interface DashboardQueueItem {
  id: string;
  kind: string;
  related_id: string | null;
  subject_type?: string;
  item_type?: string;
  label?: string;
  title?: string;
  display_name?: string;
  status?: string | null;
  due_at?: string | null;
  service_case_id?: string | null;
  finding_id?: string | null;
  report_id?: string | null;
  account_id?: string | null;
  allowed_actions?: string[];
}

export interface DashboardOverview {
  view: DashboardView;
  as_of: string;
  metrics: Record<string, number>;
  queues: Record<string, DashboardQueueItem[]>;
  allowed_actions: string[];
  boundaries?: string[];
}

export type CrmStage = "lead" | "active" | "dormant" | "closed";
export type ContactStatus = "active" | "inactive";
export type FollowUpChannel = "onsite" | "meeting" | "phone" | "internal_note";

export interface CrmAccount {
  id: string;
  display_name: string;
  stage: CrmStage;
  owner_user_id: string | null;
  industry_note: string | null;
  region_note: string | null;
  next_follow_up_at: string | null;
  contact_count?: number;
  follow_up_count?: number;
  last_follow_up_at?: string | null;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
}

export interface CrmContact {
  id: string;
  account_id: string;
  display_name: string;
  role_title: string | null;
  email: string | null;
  phone: string | null;
  status: ContactStatus;
  created_at?: string;
  updated_at?: string;
  allowed_actions: string[];
}

export interface CrmFollowUp {
  id: string;
  account_id: string;
  channel: FollowUpChannel;
  summary: string;
  next_action: string | null;
  next_due_at: string | null;
  occurred_at: string;
  actor_user_id: string;
  created_at?: string;
  allowed_actions?: string[];
}

export interface CrmAccountDetail extends CrmAccount {
  contacts: CrmContact[];
  follow_ups: CrmFollowUp[];
}

export interface CrmAccountCollection {
  items: CrmAccount[];
  next_cursor?: string | null;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface CreateCrmAccountInput {
  display_name: string;
  stage: CrmStage;
  owner_user_id?: string | null;
  industry_note?: string | null;
  region_note?: string | null;
  next_follow_up_at?: string | null;
}

export type UpdateCrmAccountInput = Partial<CreateCrmAccountInput>;

export interface CreateCrmContactInput {
  display_name: string;
  role_title?: string | null;
  email?: string | null;
  phone?: string | null;
  status: ContactStatus;
}

export type UpdateCrmContactInput = Partial<CreateCrmContactInput>;

export interface CreateCrmFollowUpInput {
  channel: FollowUpChannel;
  summary: string;
  next_action?: string | null;
  next_due_at?: string | null;
  occurred_at: string;
}

export type ReportStatus = "active" | "archived";
export type ReportVersionLifecycle = "current" | "superseded" | "void";

export interface BusinessReport {
  id: string;
  enterprise_id?: string;
  service_case_id: string;
  title: string;
  status: ReportStatus;
  current_version_no: number;
  version_count?: number;
  created_by_user_id?: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface BusinessReportCollection {
  items: BusinessReport[];
  next_cursor?: string | null;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface ReportArtifactMetadata {
  id: string;
  report_version_id: string;
  artifact_kind: "canonical_json";
  storage_kind: "database_snapshot";
  content_type: "application/json";
  status: "ready";
  sha256: string;
  size_bytes: number;
  created_at: string;
}

export interface ReportVersionSummary {
  id: string;
  report_id: string;
  version_number: number;
  lifecycle: ReportVersionLifecycle;
  change_note: string | null;
  snapshot_sha256: string;
  snapshot_size_bytes: number;
  source_counts: Record<string, number>;
  created_by_user_id?: string;
  captured_at: string;
  artifact?: ReportArtifactMetadata | null;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface BusinessReportDetail extends BusinessReport {
  versions: ReportVersionSummary[];
}

export interface ReportVersionDetail extends ReportVersionSummary {
  canonical_snapshot?: unknown;
  snapshot_summary?: Record<string, number | string | null>;
  artifact: ReportArtifactMetadata | null;
}

export interface CreateBusinessReportInput {
  service_case_id: string;
  title: string;
}

export interface CreateReportVersionInput {
  change_note?: string | null;
  document_version_ids?: string[];
}

export interface P4ErrorEnvelope {
  detail?:
    | string
    | {
        code?: string;
        retryable?: boolean;
      };
}
