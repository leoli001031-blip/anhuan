// HttpAnalysisReportApi：默认实现，直连后端。
// 报告域端点严格遵循冻结合同 v1；问答/客户/材料复用基线已有路由。
// 身份：Authorization: Bearer + 请求开始时冻结的企业快照（X-Enterprise-Id）。
// localStorage 不是权威；企业切换会中止在途请求。session.enterprise_id 与请求头不一致则 fail-closed。
// 不得从 URL/正文指定客户或租户身份；问答 request_id 由 adapter 本地生成。
import { getTenantSnapshot, tenantFetch } from "../api";
import { ApiError } from "./errors";
import type {
  AnalysisReportApi,
  HtmlReportArtifact,
  TransitionAction,
  TransitionEvidence,
} from "./AnalysisReportApi";
import { normalizeTransitionEvidence } from "./AnalysisReportApi";
import type {
  ClientAccount,
  ClientStage,
  ExceptionItem,
  GenerationAcceptedV1,
  JobStatusV1,
  MaterialItem,
  MaterialStatus,
  ProviderReportSummaryV1,
  PublishedReportDetailV1,
  PublishedReportSummaryV1,
  QaAnswer,
  SessionAccessV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";
import type { SessionAccess } from "./SessionAccess";
import {
  parseGeneration,
  parseJobStatus,
  parseProviderList,
  parseProviderSummary,
  parsePublishedDetail,
  parsePublishedList,
  parseSessionAccess,
  parseVersionDetail,
  parseVersionHistory,
  managementHealthFromHttp,
} from "./wire";
import type { ManagementHealthSnapshot } from "../features/managementHealth";
import { toUiHealthSnapshot } from "../features/managementHealth";

interface RawCrmAccount {
  id: string;
  display_name: string;
  stage: ClientStage;
  industry_note: string | null;
  region_note: string | null;
  updated_at: string;
  next_follow_up_at: string | null;
}

function toClient(raw: RawCrmAccount): ClientAccount {
  return {
    id: raw.id,
    name: raw.display_name,
    stage: raw.stage,
    industryNote: raw.industry_note?.trim() || null,
    regionNote: raw.region_note?.trim() || null,
    updatedAt: raw.updated_at,
    nextFollowUpAt: raw.next_follow_up_at ?? null,
  };
}

interface RawDocument {
  id: string;
  display_name: string;
  status: MaterialStatus;
  version_count: number;
  updated_at: string;
}

function toMaterial(raw: RawDocument): MaterialItem {
  return {
    id: raw.id,
    name: raw.display_name,
    status: raw.status,
    versionCount: raw.version_count,
    updatedAt: raw.updated_at,
  };
}

// 响应身份闭合：响应对象 ID 必须与请求一致，否则按合同错误 fail-closed。
function assertResponseId(expected: string, actual: string): void {
  if (expected !== actual) {
    throw new ApiError(409, "RESPONSE_ID_MISMATCH", false);
  }
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/;
const REFUSAL_RE = /^[A-Z][A-Z0-9_]{0,79}$/;
const QA_RESPONSE_KEYS = new Set(["answer", "citations", "refusal_reason", "request_id"]);
const QA_CITATION_KEYS = new Set([
  "canonical_unit_id",
  "document_record_id",
  "document_version_id",
  "document_name",
  "version_number",
  "source_sha256",
  "page_number",
  "body_sha256",
  "snippet",
]);

function qaContractError(): never {
  throw new ApiError(0, "MATERIAL_QA_CONTRACT_INVALID", false);
}

function qaRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    qaContractError();
  }
  return value as Record<string, unknown>;
}

function hasExactKeys(row: Record<string, unknown>, expected: Set<string>): boolean {
  const keys = Object.keys(row);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function qaString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value.trim() === "") qaContractError();
  return value;
}

function qaUuid(row: Record<string, unknown>, key: string): string {
  const value = qaString(row, key);
  if (!UUID_RE.test(value)) qaContractError();
  return value;
}

function qaInt(row: Record<string, unknown>, key: string): number {
  const value = row[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    qaContractError();
  }
  return value;
}

function parseQaCitation(value: unknown): QaAnswer["citations"][number] {
  const row = qaRecord(value);
  if (!hasExactKeys(row, QA_CITATION_KEYS)) qaContractError();
  qaUuid(row, "canonical_unit_id");
  qaUuid(row, "document_record_id");
  qaUuid(row, "document_version_id");
  if (typeof row.source_sha256 !== "string" || !SHA256_RE.test(row.source_sha256)) {
    qaContractError();
  }
  if (typeof row.body_sha256 !== "string" || !SHA256_RE.test(row.body_sha256)) {
    qaContractError();
  }
  return {
    documentName: qaString(row, "document_name"),
    versionNumber: qaInt(row, "version_number"),
    pageNumber: qaInt(row, "page_number"),
    snippet: qaString(row, "snippet"),
  };
}

function parseQaAnswer(status: number, payload: unknown, requestId: string): QaAnswer {
  if (status !== 200 && status !== 202) qaContractError();
  const row = qaRecord(payload);
  if (!hasExactKeys(row, QA_RESPONSE_KEYS)) qaContractError();
  if (qaUuid(row, "request_id") !== requestId || !Array.isArray(row.citations)) {
    qaContractError();
  }
  const citations = row.citations.map(parseQaCitation);
  const answer = row.answer;
  const refusal = row.refusal_reason;
  if (answer !== null && (typeof answer !== "string" || answer.trim() === "")) {
    qaContractError();
  }
  if (refusal !== null && (typeof refusal !== "string" || !REFUSAL_RE.test(refusal))) {
    qaContractError();
  }
  if (status === 202) {
    if (answer !== null || citations.length !== 0 || refusal !== "REQUEST_IN_PROGRESS") {
      qaContractError();
    }
    return { answer: null, refusal: false, inProgress: true, citations: [] };
  }
  if (refusal !== null) {
    if (refusal === "REQUEST_IN_PROGRESS" || answer !== null || citations.length !== 0) {
      qaContractError();
    }
    return { answer: null, refusal: true, inProgress: false, citations: [] };
  }
  if (typeof answer !== "string" || citations.length === 0) qaContractError();
  return { answer, refusal: false, inProgress: false, citations };
}

function safeHtmlFilename(contentDisposition: string | null): string | null {
  if (!contentDisposition) return null;
  let candidate: string | null = null;
  const extended = contentDisposition.match(
    /(?:^|;)\s*filename\*\s*=\s*UTF-8'[^']*'([^;]+)/i,
  );
  if (extended) {
    try {
      candidate = decodeURIComponent(extended[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return null;
    }
  } else {
    const quoted = contentDisposition.match(
      /(?:^|;)\s*filename\s*=\s*"((?:\\.|[^"\\])*)"/i,
    );
    if (quoted) {
      candidate = quoted[1].replace(/\\(["\\])/g, "$1");
    } else {
      const token = contentDisposition.match(/(?:^|;)\s*filename\s*=\s*([^;]+)/i);
      candidate = token?.[1].trim() ?? null;
    }
  }
  if (!candidate) return null;
  const normalized = candidate.normalize("NFC");
  const hasUnsafeCharacter = [...normalized].some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return (
      codePoint <= 31 ||
      codePoint === 127 ||
      "/\\<>:\"|?*".includes(character) ||
      (codePoint >= 0x202a && codePoint <= 0x202e) ||
      (codePoint >= 0x2066 && codePoint <= 0x2069)
    );
  });
  if (
    normalized !== normalized.trim() ||
    normalized.length > 160 ||
    normalized.startsWith(".") ||
    !normalized.toLowerCase().endsWith(".html") ||
    hasUnsafeCharacter
  ) {
    return null;
  }
  return normalized;
}

export class HttpAnalysisReportApi implements AnalysisReportApi, SessionAccess {
  private readonly getToken: () => string | null;
  private readonly qaRequestIds = new Map<string, string>();
  private qaTenantGeneration: number | null = null;

  constructor(getToken: () => string | null) {
    this.getToken = getToken;
  }

  private async request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      form?: FormData;
      extraHeaders?: Record<string, string>;
    } = {},
  ): Promise<{ status: number; payload: T; enterpriseId: string | null }> {
    const result = await tenantFetch<T>(path, {
      method: options.method,
      token: this.getToken(),
      body: options.body,
      form: options.form,
      extraHeaders: options.extraHeaders,
      parse: options.form || options.body !== undefined ? "json" : "json",
    });
    return {
      status: result.status,
      payload: result.payload,
      enterpriseId: result.enterpriseId,
    };
  }

  private async htmlArtifact(
    path: string,
    fallbackFilename: string,
  ): Promise<HtmlReportArtifact> {
    const result = await tenantFetch(path, {
      token: this.getToken(),
      parse: "response",
    });
    const response = result.response;
    if (!response) throw new ApiError(0, "REPORT_ARTIFACT_RESPONSE_MISSING", true);
    if (response.status !== 200) {
      throw new ApiError(
        response.status,
        "REPORT_ARTIFACT_HTTP_INVALID",
        response.status >= 500,
      );
    }
    const contentType = (response.headers.get("Content-Type") ?? "")
      .split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (contentType !== "text/html") {
      throw new ApiError(response.status, "REPORT_ARTIFACT_CONTENT_TYPE_INVALID", false);
    }
    const blob = await response.blob();
    if (blob.size === 0) {
      throw new ApiError(response.status, "REPORT_ARTIFACT_EMPTY", false);
    }
    return {
      blob,
      filename:
        safeHtmlFilename(response.headers.get("Content-Disposition")) ?? fallbackFilename,
    };
  }

  // —— 身份面 ——

  async getSessionAccess(): Promise<SessionAccessV1> {
    const { payload, enterpriseId } = await this.request<unknown>("/v1/session/access");
    const session = parseSessionAccess(payload);
    if (enterpriseId == null || session.enterprise_id !== enterpriseId) {
      throw new ApiError(403, "SESSION_ENTERPRISE_MISMATCH", false);
    }
    return session;
  }

  // —— 客户端 · 智能问答（既有 POST /api/v1/material-qa） ——

  async ask(rawQuestion: string): Promise<QaAnswer> {
    const question = rawQuestion.trim();
    if (!question) throw new ApiError(422, "EMPTY_QUESTION", false);
    const tenant = getTenantSnapshot();
    if (!tenant.ready || tenant.enterpriseId === null) {
      throw new ApiError(0, "TENANT_SNAPSHOT_UNREADY", false);
    }
    if (this.qaTenantGeneration !== tenant.generation) {
      this.qaRequestIds.clear();
      this.qaTenantGeneration = tenant.generation;
    }
    const requestKey = `${tenant.enterpriseId}\u0000${question}`;
    const requestId = this.qaRequestIds.get(requestKey) ?? crypto.randomUUID();
    this.qaRequestIds.set(requestKey, requestId);
    const { status, payload } = await this.request<unknown>("/v1/material-qa", {
      method: "POST",
      body: { question, request_id: requestId },
    });
    const answer = parseQaAnswer(status, payload, requestId);
    if (!answer.inProgress) this.qaRequestIds.delete(requestKey);
    return answer;
  }

  // —— 客户端 · 已发布报告（合同 §Client） ——

  async listPublishedReports(): Promise<PublishedReportSummaryV1[]> {
    const { payload } = await this.request<unknown>("/v1/analysis-reports/published");
    return parsePublishedList(payload);
  }

  async getPublishedReport(reportId: string): Promise<PublishedReportDetailV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/published/${encodeURIComponent(reportId)}`,
    );
    const detail = parsePublishedDetail(payload);
    assertResponseId(reportId, detail.report_id);
    return detail;
  }

  getPublishedHtmlArtifact(reportId: string): Promise<HtmlReportArtifact> {
    const suffix = UUID_RE.test(reportId) ? reportId : "published";
    return this.htmlArtifact(
      `/v1/analysis-reports/published/${encodeURIComponent(reportId)}/artifact.html`,
      `aeco-published-report-${suffix}.html`,
    );
  }

  async getLatestManagementHealth(): Promise<ManagementHealthSnapshot | null> {
    const { status, payload } = await this.request<unknown>(
      "/v1/analysis-reports/health/latest",
    );
    const envelope = managementHealthFromHttp(status, payload);
    if (envelope.snapshot === null) return null;
    return toUiHealthSnapshot(envelope.snapshot);
  }

  // —— 运营台 · 客户企业（既有 /api/v1/views-reports/crm/accounts） ——

  async listClients(): Promise<ClientAccount[]> {
    const { payload } = await this.request<{ items: RawCrmAccount[] }>(
      "/v1/views-reports/crm/accounts",
    );
    return (payload.items ?? []).map(toClient);
  }

  async getClient(clientId: string): Promise<ClientAccount> {
    const { payload } = await this.request<RawCrmAccount>(
      `/v1/views-reports/crm/accounts/${encodeURIComponent(clientId)}`,
    );
    return toClient(payload);
  }

  async createClient(input: { name: string; stage: ClientStage }): Promise<ClientAccount> {
    const { payload } = await this.request<RawCrmAccount>(
      "/v1/views-reports/crm/accounts",
      {
        method: "POST",
        body: { display_name: input.name, stage: input.stage },
      },
    );
    return toClient(payload);
  }

  // —— 运营台 · 材料（既有 /api/v1/ingestion/documents；共享与客户从不聚合） ——

  private async listMaterials(query: string): Promise<MaterialItem[]> {
    const { payload } = await this.request<{ items: RawDocument[] }>(
      `/v1/ingestion/documents?${query}`,
    );
    return (payload.items ?? []).map(toMaterial);
  }

  listSharedMaterials(): Promise<MaterialItem[]> {
    return this.listMaterials("scope_kind=service_provider&limit=100");
  }

  listClientMaterials(clientId: string): Promise<MaterialItem[]> {
    return this.listMaterials(
      `scope_kind=client&client_account_id=${encodeURIComponent(clientId)}&limit=100`,
    );
  }

  async uploadMaterial(input: {
    file: File;
    name: string;
    scope: "shared" | "client";
    clientId?: string;
  }): Promise<void> {
    const form = new FormData();
    form.set("display_name", input.name);
    form.set("declared_material_kind", "unknown");
    form.set(
      "knowledge_scope_kind",
      input.scope === "shared" ? "service_provider" : "client",
    );
    if (input.scope === "client" && input.clientId) {
      form.set("client_account_id", input.clientId);
    }
    form.set("file", input.file, input.file.name);
    await this.request<unknown>("/v1/ingestion/documents", {
      method: "POST",
      form,
      extraHeaders: { "Idempotency-Key": crypto.randomUUID() },
    });
  }

  // —— 运营台 · 报告工作流（合同 §Provider） ——

  async listClientReports(clientId: string): Promise<ProviderReportSummaryV1[]> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/clients/${encodeURIComponent(clientId)}/reports`,
    );
    return parseProviderList(payload);
  }

  async createReport(clientId: string, requestId: string): Promise<ProviderReportSummaryV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/clients/${encodeURIComponent(clientId)}/reports`,
      { method: "POST", body: { request_id: requestId } },
    );
    return parseProviderSummary(payload);
  }

  async generate(
    clientId: string,
    reportId: string,
    requestId: string,
  ): Promise<GenerationAcceptedV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/clients/${encodeURIComponent(clientId)}/reports/${encodeURIComponent(reportId)}/generations`,
      { method: "POST", body: { request_id: requestId } },
    );
    return parseGeneration(payload);
  }

  async getJob(jobId: string): Promise<JobStatusV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/jobs/${encodeURIComponent(jobId)}`,
    );
    const job = parseJobStatus(payload);
    assertResponseId(jobId, job.job_id);
    return job;
  }

  async getVersion(versionId: string): Promise<VersionDetailV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/versions/${encodeURIComponent(versionId)}`,
    );
    const detail = parseVersionDetail(payload);
    assertResponseId(versionId, detail.version_id);
    return detail;
  }

  getVersionHtmlArtifact(versionId: string): Promise<HtmlReportArtifact> {
    const suffix = UUID_RE.test(versionId) ? versionId : "version";
    return this.htmlArtifact(
      `/v1/analysis-reports/versions/${encodeURIComponent(versionId)}/artifact.html`,
      `aeco-analysis-report-${suffix}.html`,
    );
  }

  async listVersions(reportId: string): Promise<VersionHistoryItemV1[]> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/${encodeURIComponent(reportId)}/versions`,
    );
    return parseVersionHistory(payload);
  }

  async transition(
    versionId: string,
    action: TransitionAction,
    evidence?: TransitionEvidence,
  ): Promise<ProviderReportSummaryV1> {
    const body = normalizeTransitionEvidence(action, evidence);
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/versions/${encodeURIComponent(versionId)}/${action}`,
      body ? { method: "POST", body } : { method: "POST" },
    );
    return parseProviderSummary(payload);
  }

  // —— 运营台 · 异常中心 ——

  listExceptions(): Promise<ExceptionItem[]> {
    // 冻结合同 v1 未定义异常列表端点，后端基线也不存在对应路由。
    // 不猜测 API：明确以 503 语义上报，由页面呈现“尚未接入”专属状态。
    return Promise.reject(new ApiError(503, "EXCEPTIONS_NOT_CONTRACTED", true));
  }
}
