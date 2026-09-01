// 合同类型：严格对应 artifacts/material-rag-analysis-report-contract-v1（frozen 2026-08-21）。
// 前端 UI 类型只暴露业务可读字段；dataset/chunk/scope/lease/物理存储 ID 永不进入 UI 类型。

export const SESSION_SCHEMA = "anhuan-analysis-report-session-v1" as const;
export const TEMPLATE_TITLE = "企业安环资料分析报告";

export type ProductRole = "provider_admin" | "client_user";

export type Capability =
  | "list_client_reports"
  | "create_report"
  | "generate"
  | "review"
  | "publish"
  | "withdraw"
  | "list_published"
  | "read_published";

export interface SessionAccessV1 {
  schema: typeof SESSION_SCHEMA;
  product_role: ProductRole;
  enterprise_id: string;
  template_id: string;
  template_title: string;
  capabilities: Capability[];
}

// —— 客户端已发布报告 ——

export interface PublishedReportSummaryV1 {
  report_id: string;
  version_id: string;
  version_number: number;
  title: string;
  published_at: string;
  artifact_ready: true;
}

export type SectionKey =
  | "source_scope"
  | "status_summary"
  | "key_findings"
  | "risks_and_gaps"
  | "remediation"
  | "citations"
  | "usage_boundary";

// 七章顺序冻结（合同 §Client）
export const SECTION_ORDER: ReadonlyArray<{ key: SectionKey; title: string }> = [
  { key: "source_scope", title: "资料范围" },
  { key: "status_summary", title: "现状摘要" },
  { key: "key_findings", title: "主要发现" },
  { key: "risks_and_gaps", title: "风险与缺口" },
  { key: "remediation", title: "整改建议" },
  { key: "citations", title: "引用证据" },
  { key: "usage_boundary", title: "使用边界" },
];

export interface SectionV1 {
  key: SectionKey;
  title: string;
  body: string;
}

// 抽屉定位保留 citation_id / document_version_id；UI 只展示文档名、版本、页码、摘录。
export interface CitationV1 {
  citation_id: string;
  document_version_id: string;
  documentName: string;
  versionNumber: number;
  pageNumber: number;
  excerpt: string;
}

export interface PublishedReportDetailV1 {
  schema: "anhuan-analysis-report-published-detail-v1";
  report_id: string;
  version_id: string;
  version_number: number;
  title: string;
  published_at: string;
  artifact_ready: true;
  sections: SectionV1[];
  citations: CitationV1[];
}

// —— 甲方运营台报告工作流 ——

export type ReportStatus =
  | "empty"
  | "queued"
  | "generating"
  | "draft"
  | "review_pending"
  | "changes_requested"
  | "approved"
  | "published"
  | "superseded"
  | "withdrawn"
  | "failed";

export const REPORT_STATUS_LABEL: Record<ReportStatus, string> = {
  empty: "空报告",
  queued: "排队中",
  generating: "生成中",
  draft: "草稿",
  review_pending: "审核中",
  changes_requested: "已退回",
  approved: "已批准",
  published: "已发布",
  superseded: "已被新版本替代",
  withdrawn: "已撤回",
  failed: "生成失败",
};

export interface ProviderReportSummaryV1 {
  report_id: string;
  current_version_id: string | null;
  current_status: ReportStatus;
  version_number: number;
  title: string;
  updated_at: string;
}

export interface GenerationAcceptedV1 {
  schema: "anhuan-analysis-report-generation-v1";
  job_id: string;
  version_id: string;
  status: "queued" | "generating" | "draft" | "failed";
}

export interface JobStatusV1 {
  schema: "anhuan-analysis-report-job-v1";
  job_id: string;
  version_id: string;
  status: "queued" | "generating" | "draft" | "failed";
  error_reason: string | null;
}

export interface VersionHistoryItemV1 {
  version_id: string;
  version_number: number;
  status: ReportStatus;
  created_at: string;
}

export interface VersionDetailV1 {
  schema: "anhuan-analysis-report-draft-v1";
  report_id: string;
  version_id: string;
  version_number: number;
  status: ReportStatus;
  title: string;
  sections: SectionV1[];
  citations: CitationV1[];
}

// —— 控制台业务对象（UI 视角） ——

export type ClientStage = "lead" | "active" | "dormant" | "closed";

export const CLIENT_STAGE_LABEL: Record<ClientStage, string> = {
  lead: "意向",
  active: "服务中",
  dormant: "休眠",
  closed: "已终止",
};

export interface ClientAccount {
  id: string;
  name: string;
  stage: ClientStage;
  updatedAt: string;
}

export type MaterialStatus = "processing" | "ready" | "blocked" | "failed";

export const MATERIAL_STATUS_LABEL: Record<MaterialStatus, string> = {
  processing: "处理中",
  ready: "可用",
  blocked: "受阻",
  failed: "解析失败",
};

export interface MaterialItem {
  id: string;
  name: string;
  status: MaterialStatus;
  versionCount: number;
  updatedAt: string;
}

// 异常中心只承载可行动的业务错误，永不携带原始日志/堆栈。
export interface ExceptionItem {
  id: string;
  clientName: string | null;
  kind: string;
  message: string;
  actionHint: string;
  occurredAt: string;
}

// —— 智能问答 ——

export interface QaCitation {
  documentName: string;
  versionNumber: number;
  pageNumber: number;
  snippet: string;
}

export interface QaAnswer {
  answer: string | null;
  refusal: boolean;
  inProgress: boolean;
  citations: QaCitation[];
}
