export type RehearsalPlanStatus = "draft" | "active" | "archived";
export type RehearsalCheckCategory = "service" | "dependency" | "backup" | "restore" | "security" | "rollback";
export type RehearsalRunStatus = "planned" | "running" | "passed" | "failed" | "cancelled";
export type RehearsalResultStatus = "pending" | "passed" | "failed" | "blocked";

export interface RehearsalPlan {
  id: string;
  enterprise_id: string;
  name: string;
  status: RehearsalPlanStatus;
  execution_mode: "local_manual";
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface RehearsalCheck {
  id: string;
  enterprise_id: string;
  plan_id: string;
  check_key: string;
  category: RehearsalCheckCategory;
  label: string;
  sequence_no: number;
  required: boolean;
  enabled: boolean;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface RehearsalRun {
  id: string;
  enterprise_id: string;
  plan_id: string;
  status: RehearsalRunStatus;
  total_count: number;
  pending_count: number;
  passed_count: number;
  failed_count: number;
  blocked_count: number;
  rollback_required: boolean;
  created_by_user_id: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface RehearsalCheckResult {
  id: string;
  enterprise_id: string;
  run_id: string;
  check_id: string;
  check_key: string;
  category: RehearsalCheckCategory;
  label: string;
  sequence_no: number;
  required: boolean;
  status: RehearsalResultStatus;
  reason_code: string | null;
  evidence_sha256: string | null;
  recorded_by_user_id: string | null;
  recorded_at: string | null;
  created_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface RehearsalPlanDetail extends RehearsalPlan {
  checks: RehearsalCheck[];
  recent_runs: RehearsalRun[];
}

export interface RehearsalRunDetail extends RehearsalRun {
  results: RehearsalCheckResult[];
}

export interface RehearsalPlanCollection {
  items: RehearsalPlan[];
  allowed_actions: string[];
  boundaries?: string[];
}

export interface LocalRehearsalDashboard {
  rehearsal_label: string;
  plan_counts: { total: number; draft: number; active: number; archived: number };
  run_counts: { total: number; planned: number; running: number; passed: number; failed: number; cancelled: number };
  result_counts: { total: number; pending: number; passed: number; failed: number; blocked: number };
  rollback_required_count: number;
  pending_plans: RehearsalPlan[];
  recent_runs: RehearsalRun[];
  allowed_actions: string[];
  boundaries: string[];
}

export interface CreateRehearsalPlanInput {
  name: string;
}

export interface CreateRehearsalCheckInput {
  check_key: string;
  category: RehearsalCheckCategory;
  label: string;
  sequence_no: number;
  required: boolean;
  enabled?: boolean;
}

export interface UpdateRehearsalCheckInput {
  category?: RehearsalCheckCategory;
  label?: string;
  sequence_no?: number;
  required?: boolean;
  enabled?: boolean;
}

export interface RecordRehearsalResultInput {
  status: "passed" | "failed" | "blocked";
  reason_code: "MANUAL_CHECK_PASSED" | "MANUAL_CHECK_FAILED" | "MANUAL_CHECK_BLOCKED";
  evidence_sha256: string;
}

export interface P7ErrorEnvelope {
  detail?: string | { code?: string; retryable?: boolean };
}
