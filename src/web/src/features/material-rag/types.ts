export type MaterialScopeKind = "service_provider" | "client";

export type MaterialQaPhase =
  | "disabled"
  | "loading"
  | "empty"
  | "ready"
  | "in-progress"
  | "conflict"
  | "unavailable"
  | "denied"
  | "retry"
  | "recovery";

export const CLOSED_QUERY_IDS = [
  "provider.shared",
  "client.current",
  "combo.provider_client",
  "cross.denied",
  "fail.clear",
  "progress.wait",
] as const;

export type ClosedQueryId = (typeof CLOSED_QUERY_IDS)[number];

export interface MaterialCitation {
  canonical_unit_id: string;
  document_record_id: string;
  document_version_id: string;
  document_name: string;
  version_number: number;
  source_sha256: string;
  page_number: number;
  body_sha256: string;
  snippet: string;
  scope_kind: MaterialScopeKind;
}

export interface MaterialQaAskResult {
  answer: string | null;
  citations: MaterialCitation[];
  refusal_reason: string | null;
  request_id: string;
  scope_label?: string;
}

export interface MaterialQaUiState {
  phase: MaterialQaPhase;
  answer: string | null;
  citations: MaterialCitation[];
  code: string | null;
  scopeLabel: string;
}

export const QUERY_LABELS: Record<ClosedQueryId, string> = {
  "provider.shared": "服务商共享域",
  "client.current": "当前客户域",
  "combo.provider_client": "服务商共享域 + 当前客户域",
  "cross.denied": "跨范围拒绝（固定）",
  "fail.clear": "失败并清空旧结果",
  "progress.wait": "处理中",
};
