// AnalysisReportApi：两套产品壳的唯一数据门面。
// 报告域走冻结合同 /api/v1/analysis-reports/*；问答、客户、材料复用基线已有端点。
import type {
  ClientAccount,
  ClientStage,
  ExceptionItem,
  GenerationAcceptedV1,
  JobStatusV1,
  MaterialItem,
  ProviderReportSummaryV1,
  PublishedReportDetailV1,
  PublishedReportSummaryV1,
  QaAnswer,
  ReviewChecklistV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";
import { REVIEW_CHECKLIST_KEYS } from "./types";
import { ApiError } from "./errors";
import type { ManagementHealthSnapshot } from "../features/managementHealth";

export type TransitionAction = "submit" | "return" | "approve" | "publish" | "withdraw";

export interface TransitionEvidence {
  checklist?: ReviewChecklistV1;
  comment?: string;
}

function transitionInvalid(): never {
  throw new ApiError(409, "REPORT_TRANSITION_INVALID", false);
}

// 与后端审核合同保持同一个 fail-closed 边界：批准项必须全真，
// 退回必须有理由，其余状态迁移不携带审核证据。
export function normalizeTransitionEvidence(
  action: TransitionAction,
  evidence?: TransitionEvidence,
): TransitionEvidence | undefined {
  let checklist: ReviewChecklistV1 | undefined;
  if (evidence?.checklist !== undefined) {
    const raw = evidence.checklist as unknown;
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
      transitionInvalid();
    }
    const row = raw as Record<string, unknown>;
    if (
      Object.keys(row).length !== REVIEW_CHECKLIST_KEYS.length ||
      !REVIEW_CHECKLIST_KEYS.every((key) => typeof row[key] === "boolean")
    ) {
      transitionInvalid();
    }
    checklist = {
      citation_traceable: row.citation_traceable as boolean,
      risks_complete: row.risks_complete as boolean,
      usage_boundary: row.usage_boundary as boolean,
    };
  }

  let comment: string | undefined;
  if (evidence?.comment !== undefined) {
    if (typeof evidence.comment !== "string") transitionInvalid();
    const normalized = evidence.comment.trim();
    if (normalized.length > 2_000) transitionInvalid();
    comment = normalized || undefined;
  }

  if (action === "approve") {
    if (!checklist || !REVIEW_CHECKLIST_KEYS.every((key) => checklist[key] === true)) {
      transitionInvalid();
    }
    return comment ? { checklist, comment } : { checklist };
  }
  if (action === "return") {
    if (!comment) transitionInvalid();
    return checklist ? { checklist, comment } : { comment };
  }
  if (checklist || comment) transitionInvalid();
  return undefined;
}

export interface HtmlReportArtifact {
  blob: Blob;
  filename: string;
}

export function saveHtmlReportArtifact(artifact: HtmlReportArtifact): void {
  const objectUrl = URL.createObjectURL(artifact.blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = artifact.filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

export interface AnalysisReportApi {
  // 客户端 · 智能问答（复用既有 POST /api/v1/material-qa，不带任何身份字段）
  ask(question: string): Promise<QaAnswer>;

  // 客户端 · 已发布报告（本企业 published + artifact_ready）
  listPublishedReports(): Promise<PublishedReportSummaryV1[]>;
  getPublishedReport(reportId: string): Promise<PublishedReportDetailV1>;
  getPublishedHtmlArtifact(reportId: string): Promise<HtmlReportArtifact>;
  getLatestManagementHealth(): Promise<ManagementHealthSnapshot | null>;

  // 运营台 · 客户企业
  listClients(): Promise<ClientAccount[]>;
  getClient(clientId: string): Promise<ClientAccount>;
  createClient(input: { name: string; stage: ClientStage }): Promise<ClientAccount>;

  // 运营台 · 材料（共享域 或 指定客户域，二者从不聚合）
  listSharedMaterials(): Promise<MaterialItem[]>;
  listClientMaterials(clientId: string): Promise<MaterialItem[]>;
  uploadMaterial(input: {
    file: File;
    name: string;
    scope: "shared" | "client";
    clientId?: string;
  }): Promise<void>;

  // 运营台 · 报告工作流（合同 §Provider）
  listClientReports(clientId: string): Promise<ProviderReportSummaryV1[]>;
  createReport(clientId: string, requestId: string): Promise<ProviderReportSummaryV1>;
  generate(
    clientId: string,
    reportId: string,
    requestId: string,
  ): Promise<GenerationAcceptedV1>;
  getJob(jobId: string): Promise<JobStatusV1>;
  getVersion(versionId: string): Promise<VersionDetailV1>;
  getVersionHtmlArtifact(versionId: string): Promise<HtmlReportArtifact>;
  listVersions(reportId: string): Promise<VersionHistoryItemV1[]>;
  transition(
    versionId: string,
    action: TransitionAction,
    evidence?: TransitionEvidence,
  ): Promise<ProviderReportSummaryV1>;

  // 运营台 · 异常中心（冻结合同尚未覆盖；HTTP 实现返回不可用，UI 有专属状态）
  listExceptions(): Promise<ExceptionItem[]>;
}
