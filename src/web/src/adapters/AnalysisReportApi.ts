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
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";

export type TransitionAction = "submit" | "return" | "approve" | "publish" | "withdraw";

export interface AnalysisReportApi {
  // 客户端 · 智能问答（复用既有 POST /api/v1/material-qa，不带任何身份字段）
  ask(question: string): Promise<QaAnswer>;

  // 客户端 · 已发布报告（本企业 published + artifact_ready）
  listPublishedReports(): Promise<PublishedReportSummaryV1[]>;
  getPublishedReport(reportId: string): Promise<PublishedReportDetailV1>;

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
  listVersions(reportId: string): Promise<VersionHistoryItemV1[]>;
  transition(
    versionId: string,
    action: TransitionAction,
  ): Promise<ProviderReportSummaryV1>;

  // 运营台 · 异常中心（冻结合同尚未覆盖；HTTP 实现返回不可用，UI 有专属状态）
  listExceptions(): Promise<ExceptionItem[]>;
}
