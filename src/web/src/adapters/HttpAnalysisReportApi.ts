// HttpAnalysisReportApi：默认实现，直连后端。
// 报告域端点严格遵循冻结合同 v1；问答/客户/材料复用基线已有路由，不猜测任何新端点。
// 身份：Authorization: Bearer + 旧平台 membership 规则选出的 X-Enterprise-Id。
// 不得从 URL/正文指定客户或租户身份；request_id 由调用方持有，本 adapter 不随机生成。
import { getSelectedEnterprise } from "../api";
import { ApiError } from "./errors";
import type { AnalysisReportApi, TransitionAction } from "./AnalysisReportApi";
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
} from "./wire";

const API = "/api";

interface RawCrmAccount {
  id: string;
  display_name: string;
  stage: ClientStage;
  updated_at: string;
}

function toClient(raw: RawCrmAccount): ClientAccount {
  return {
    id: raw.id,
    name: raw.display_name,
    stage: raw.stage,
    updatedAt: raw.updated_at,
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

export class HttpAnalysisReportApi implements AnalysisReportApi, SessionAccess {
  private readonly getToken: () => string | null;

  constructor(getToken: () => string | null) {
    this.getToken = getToken;
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown; form?: FormData } = {},
  ): Promise<{ status: number; payload: T }> {
    const headers: Record<string, string> = {};
    const token = this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const enterpriseId = getSelectedEnterprise();
    if (enterpriseId) headers["X-Enterprise-Id"] = enterpriseId;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";

    let resp: Response;
    try {
      resp = await fetch(`${API}${path}`, {
        method: options.method ?? "GET",
        headers,
        body:
          options.form ??
          (options.body !== undefined ? JSON.stringify(options.body) : undefined),
      });
    } catch {
      throw new ApiError(0, "NETWORK_ERROR", true);
    }

    if (!resp.ok) {
      let code = `HTTP_${resp.status}`;
      let retryable = resp.status >= 500;
      const contentType = resp.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        try {
          const detail = ((await resp.json()) as { detail?: unknown }).detail;
          if (typeof detail === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(detail)) {
            code = detail;
          } else if (detail && typeof detail === "object") {
            const structured = detail as { code?: unknown; retryable?: unknown };
            if (typeof structured.code === "string") code = structured.code;
            if (typeof structured.retryable === "boolean") retryable = structured.retryable;
          }
        } catch {
          // 保持状态码兜底
        }
      }
      throw new ApiError(resp.status, code, retryable);
    }
    if (resp.status === 204) return { status: resp.status, payload: undefined as T };
    return { status: resp.status, payload: (await resp.json()) as T };
  }

  // —— 身份面 ——

  async getSessionAccess(): Promise<SessionAccessV1> {
    const { payload } = await this.request<unknown>("/v1/session/access");
    return parseSessionAccess(payload);
  }

  // —— 客户端 · 智能问答（既有 POST /api/v1/material-qa） ——

  async ask(_question: string): Promise<QaAnswer> {
    return Promise.reject(new ApiError(503, "CLIENT_QA_AUTH_BINDING_PENDING", true));
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
    return parsePublishedDetail(payload);
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
    const headers: Record<string, string> = {
      "Idempotency-Key": crypto.randomUUID(),
    };
    const token = this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const enterpriseId = getSelectedEnterprise();
    if (enterpriseId) headers["X-Enterprise-Id"] = enterpriseId;
    let resp: Response;
    try {
      resp = await fetch(`${API}/v1/ingestion/documents`, {
        method: "POST",
        headers,
        body: form,
      });
    } catch {
      throw new ApiError(0, "NETWORK_ERROR", true);
    }
    if (!resp.ok) {
      throw new ApiError(resp.status, `HTTP_${resp.status}`, resp.status >= 500);
    }
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
    return parseJobStatus(payload);
  }

  async getVersion(versionId: string): Promise<VersionDetailV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/versions/${encodeURIComponent(versionId)}`,
    );
    return parseVersionDetail(payload);
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
  ): Promise<ProviderReportSummaryV1> {
    const { payload } = await this.request<unknown>(
      `/v1/analysis-reports/versions/${encodeURIComponent(versionId)}/${action}`,
      { method: "POST" },
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
