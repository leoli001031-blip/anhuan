export type QualityCategory = "ingestion" | "retrieval" | "qa" | "authorization" | "injection";
export type QualitySuiteStatus = "active" | "archived";
export type ScenarioType = "exact_match" | "threshold" | "refusal_required" | "isolation_required" | "injection_blocked" | "disagreement_max";
export type ScenarioSeverity = "low" | "medium" | "high" | "critical";
export type QualityRunStatus = "queued" | "running" | "passed" | "failed" | "cancelled";
export type QualityResultStatus = "passed" | "failed" | "error";
export type DisagreementKind = "parser" | "ocr" | "citation" | "refusal" | "authorization" | "injection";
export type DisagreementReviewStatus = "open" | "acknowledged" | "waived";

export type LimitedMetricValue = number | boolean | string | null;
export type LimitedMetricObject = Record<string, LimitedMetricValue>;

export interface QualitySuite {
  id: string;
  enterprise_id: string;
  name: string;
  category: QualityCategory;
  status: QualitySuiteStatus;
  scenario_count?: number;
  run_count?: number;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualityScenario {
  id: string;
  enterprise_id: string;
  suite_id: string;
  scenario_key: string;
  scenario_type: ScenarioType;
  severity: ScenarioSeverity;
  oracle_config: LimitedMetricObject;
  synthetic_observation: LimitedMetricObject;
  scenario_sha256: string;
  enabled: boolean;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualityRun {
  id: string;
  enterprise_id: string;
  suite_id: string;
  suite_name?: string;
  status: QualityRunStatus;
  trigger_kind: "manual";
  total_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  created_by_user_id: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualityResult {
  id: string;
  enterprise_id: string;
  run_id: string;
  scenario_id: string;
  scenario_key?: string;
  scenario_type?: ScenarioType;
  severity?: ScenarioSeverity;
  status: QualityResultStatus;
  reason_code: string;
  observed_metrics: LimitedMetricObject;
  evidence_sha256: string;
  created_at: string;
  disagreements: QualityDisagreement[];
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualitySuiteDetail extends QualitySuite {
  scenarios: QualityScenario[];
  runs: QualityRun[];
}

export interface QualityRunDetail extends QualityRun {
  results: QualityResult[];
}

export interface QualitySuiteCollection {
  items: QualitySuite[];
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualityDisagreement {
  id: string;
  enterprise_id: string;
  result_id: string;
  kind: DisagreementKind;
  left_digest: string;
  right_digest: string;
  score: number;
  review_status: DisagreementReviewStatus;
  review_note: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  created_at: string;
  allowed_actions: string[];
  boundaries?: string[];
}

export interface QualityDisagreementCollection {
  items: QualityDisagreement[];
  count: number;
  open_count: number;
  boundaries?: string[];
}

export interface QualityDashboard {
  synthetic_label: string;
  suite_counts: { total: number; active: number; archived: number };
  scenario_counts: { total: number; enabled: number; disabled: number };
  run_counts: {
    total: number;
    queued: number;
    running: number;
    passed: number;
    failed: number;
    cancelled: number;
  };
  result_counts: { total: number; passed: number; failed: number; error: number };
  disagreement_counts: { total: number; open: number; acknowledged: number; waived: number };
  recent_runs: QualityRun[];
  allowed_actions: string[];
  boundaries: string[];
}

export interface CreateQualitySuiteInput {
  name: string;
  category: QualityCategory;
}

export interface CreateQualityScenarioInput {
  scenario_key: string;
  scenario_type: ScenarioType;
  severity: ScenarioSeverity;
  oracle_config: LimitedMetricObject;
  synthetic_observation: LimitedMetricObject;
  enabled?: boolean;
}

export interface UpdateQualityScenarioInput {
  severity?: ScenarioSeverity;
  oracle_config?: LimitedMetricObject;
  synthetic_observation?: LimitedMetricObject;
  enabled?: boolean;
}

export interface ReviewDisagreementInput {
  review_status: "acknowledged" | "waived";
  review_note: string;
}

export interface P6ErrorEnvelope {
  detail?: string | { code?: string; retryable?: boolean };
}
