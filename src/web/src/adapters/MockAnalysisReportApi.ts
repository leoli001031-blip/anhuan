// MockAnalysisReportApi：本地合成数据，仅用于无后端的视觉/交互走查。
// 仅在 import.meta.env.DEV && VITE_MATERIAL_RAG_REPORT_MOCK === "1" 时由工厂启用（见 index.ts），
// 组件不直接引用本文件；启用时所有页面显示“本地合成数据”标记。
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
  ProviderReportSummaryV1,
  PublishedReportDetailV1,
  PublishedReportSummaryV1,
  QaAnswer,
  ReviewEventV1,
  ReportStatus,
  SessionAccessV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";
import type { SessionAccess } from "./SessionAccess";
import {
  SYNTHETIC_MANAGEMENT_HEALTH,
  type ManagementHealthSnapshot,
} from "../features/managementHealth";

const delay = (ms = 260) => new Promise((resolve) => setTimeout(resolve, ms));

const CLIENT_A_ID = "22222222-2222-4222-8222-222222222222";
const CLIENT_B_ID = "33333333-3333-4333-8333-333333333333";

interface MockVersion {
  versionId: string;
  versionNumber: number;
  status: ReportStatus;
  createdAt: string;
  reviewEvents: ReviewEventV1[];
}

interface MockReport {
  reportId: string;
  clientId: string;
  versions: MockVersion[];
  updatedAt: string;
}

const SYNTHETIC_SECTIONS = [
  { key: "source_scope", title: "资料范围", body: "本报告基于服务商共享资料 3 份与本企业指定资料 4 份编制，全部为已发布的当前版本。" },
  { key: "status_summary", title: "现状摘要", body: "企业已建立基础安环管理制度，台账记录总体完整，现场执行记录存在缺项。" },
  { key: "key_findings", title: "主要发现", body: "废气治理设施运行记录与台账一致；危废暂存间标识与台账存在两处不一致。" },
  { key: "risks_and_gaps", title: "风险与缺口", body: "应急预案未覆盖夜班值班场景；部分培训记录缺少签字确认。" },
  { key: "remediation", title: "整改建议", body: "建议 30 日内完成危废台账与现场标识核对，补齐应急预案夜班附录并组织一次培训补签。" },
  { key: "citations", title: "引用证据", body: "以上结论均可溯源至下列材料的已发布版本页码。" },
  { key: "usage_boundary", title: "使用边界", body: "本报告基于所列材料生成，仅供内部管理参考，不作为执法或生产放行依据。" },
] as const;

const SYNTHETIC_CITATIONS = [
  {
    citation_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    document_version_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    documentName: "安全生产责任制（发布版）",
    versionNumber: 2,
    pageNumber: 3,
    excerpt: "企业应当建立安全生产责任制。",
  },
  {
    citation_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc2",
    document_version_id: "dddddddd-dddd-4ddd-8ddd-ddddddddddd2",
    documentName: "危废台账 2026 年上半年",
    versionNumber: 1,
    pageNumber: 7,
    excerpt: "危废出入库记录与暂存间标识应当一致。",
  },
  {
    citation_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc3",
    document_version_id: "dddddddd-dddd-4ddd-8ddd-ddddddddddd3",
    documentName: "突发环境事件应急预案",
    versionNumber: 3,
    pageNumber: 12,
    excerpt: "预案应覆盖全部班次与值班场景。",
  },
];

function escapeHtml(value: string | number): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function mockHtmlArtifact(
  report: Pick<
    PublishedReportDetailV1 | VersionDetailV1,
    "title" | "version_number" | "sections" | "citations"
  >,
  filename: string,
): HtmlReportArtifact {
  const sections = report.sections
    .map(
      (section) =>
        `<section><h2>${escapeHtml(section.title)}</h2><div class="body">${escapeHtml(section.body)}</div></section>`,
    )
    .join("");
  const citations = report.citations
    .map(
      (citation, index) =>
        `<li>[${index + 1}] ${escapeHtml(citation.documentName)} v${citation.versionNumber} ` +
        `第${citation.pageNumber}页：${escapeHtml(citation.excerpt)}</li>`,
    )
    .join("");
  const html = `<!doctype html>
<html lang="zh-CN" data-aeco-data-mode="mock-synthetic">
<head><meta charset="utf-8"><meta name="aeco-data-mode" content="mock-synthetic">
<title>[本地合成数据] ${escapeHtml(report.title)}</title>
<style>body{max-width:900px;margin:40px auto;padding:0 28px;color:#20342c;font:16px/1.75 system-ui,sans-serif}.mock{padding:14px 18px;background:#fff1cc;border:2px solid #b66a00;font-weight:700}h1,h2{color:#164c3b}.body{white-space:pre-wrap}section{margin-top:32px}li{margin:10px 0}</style></head>
<body><p class="mock">本地合成数据 · 非真实客户报告 · 仅供界面走查</p>
<h1>${escapeHtml(report.title)}</h1><p>第 ${report.version_number} 版</p>
${sections}<section><h2>引用</h2><ol>${citations}</ol></section></body></html>`;
  return {
    blob: new Blob([html], { type: "text/html;charset=utf-8" }),
    filename,
  };
}

export class MockAnalysisReportApi implements AnalysisReportApi, SessionAccess {
  private readonly role: "provider_admin" | "client_user";
  private clients: ClientAccount[];
  private sharedMaterials: MaterialItem[];
  private clientMaterials: Record<string, MaterialItem[]>;
  private reports: MockReport[];
  private exceptions: ExceptionItem[];
  private jobs = new Map<string, { versionId: string; reportId: string; pollsLeft: number }>();
  private seq = 100;
  private createByRequest = new Map<string, string>();
  private generateByRequest = new Map<string, GenerationAcceptedV1>();

  constructor() {
    this.role =
      import.meta.env.VITE_MATERIAL_RAG_REPORT_MOCK_ROLE === "client"
        ? "client_user"
        : "provider_admin";
    const now = new Date().toISOString();
    this.clients = [
      { id: CLIENT_A_ID, name: "蓝海化工有限公司", stage: "active", industryNote: null, regionNote: null, updatedAt: now, nextFollowUpAt: "2026-09-05T09:00:00+00:00" },
      { id: CLIENT_B_ID, name: "青松电子科技有限公司", stage: "lead", industryNote: null, regionNote: null, updatedAt: now, nextFollowUpAt: null },
    ];
    this.sharedMaterials = [
      { id: "m-sh-1", name: "安全生产管理制度（共享模板）", status: "ready", versionCount: 2, updatedAt: now },
      { id: "m-sh-2", name: "环保法规摘要 2026", status: "ready", versionCount: 5, updatedAt: now },
      { id: "m-sh-3", name: "废气治理工艺指引", status: "processing", versionCount: 1, updatedAt: now },
    ];
    this.clientMaterials = {
      [CLIENT_A_ID]: [
        { id: "m-a-1", name: "排污许可证", status: "ready", versionCount: 1, updatedAt: now },
        { id: "m-a-2", name: "危废台账 2026 年上半年", status: "ready", versionCount: 1, updatedAt: now },
        { id: "m-a-3", name: "突发环境事件应急预案", status: "ready", versionCount: 3, updatedAt: now },
        { id: "m-a-4", name: "旧版培训签到表", status: "failed", versionCount: 1, updatedAt: now },
      ],
      [CLIENT_B_ID]: [
        { id: "m-b-1", name: "环评批复", status: "ready", versionCount: 1, updatedAt: now },
      ],
    };
    this.reports = [
      {
        reportId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        clientId: CLIENT_A_ID,
        updatedAt: now,
        versions: [
          {
            versionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            versionNumber: 1,
            status: "superseded",
            createdAt: "2026-07-30T09:00:00+00:00",
            reviewEvents: [],
          },
          {
            versionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
            versionNumber: 2,
            status: "published",
            createdAt: "2026-08-21T10:00:00+00:00",
            reviewEvents: [],
          },
        ],
      },
      {
        reportId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
        clientId: CLIENT_A_ID,
        updatedAt: now,
        versions: [
          {
            versionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3",
            versionNumber: 1,
            status: "review_pending",
            createdAt: "2026-08-21T12:30:00+00:00",
            reviewEvents: [
              {
                event_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                action: "submit",
                checklist: {},
                comment: null,
                created_at: "2026-08-21T12:35:00+00:00",
              },
            ],
          },
        ],
      },
    ];
    this.exceptions = [
      {
        id: "ex-1",
        clientName: "蓝海化工有限公司",
        kind: "材料解析失败",
        message: "《旧版培训签到表》无法解析文本内容。",
        actionHint: "重新上传清晰的扫描件或照片版文件。",
        occurredAt: now,
      },
      {
        id: "ex-2",
        clientName: "青松电子科技有限公司",
        kind: "生成材料不足",
        message: "该客户可用材料不足，报告生成被跳过。",
        actionHint: "先补充指定客户材料，再重新生成报告。",
        occurredAt: now,
      },
    ];
  }

  private newId(prefix: string): string {
    this.seq += 1;
    return `${prefix}-${this.seq}`;
  }

  async getSessionAccess(): Promise<SessionAccessV1> {
    await delay(120);
    return {
      schema: "anhuan-analysis-report-session-v1",
      product_role: this.role,
      enterprise_id:
        this.role === "client_user"
          ? CLIENT_A_ID
          : "11111111-1111-4111-8111-111111111111",
      template_id: "enterprise-ehs-material-analysis-v1",
      template_title: "企业安环资料分析报告",
      capabilities:
        this.role === "client_user"
          ? ["list_published", "read_published"]
          : ["list_client_reports", "create_report", "generate", "review", "publish", "withdraw"],
    };
  }

  // —— 客户端 ——

  async ask(question: string): Promise<QaAnswer> {
    await delay(600);
    if (/无法回答|不存在/.test(question)) {
      return { answer: null, refusal: true, inProgress: false, citations: [] };
    }
    return {
      answer:
        "根据已纳入的材料：贵公司已建立安全生产责任制 [1]，危废台账与现场标识存在两处不一致 [2]，应急预案尚未覆盖夜班值班场景 [3]。整改建议详见最新分析报告。",
      refusal: false,
      inProgress: false,
      citations: SYNTHETIC_CITATIONS.map((c) => ({
        documentName: c.documentName,
        versionNumber: c.versionNumber,
        pageNumber: c.pageNumber,
        snippet: c.excerpt,
      })),
    };
  }

  async listPublishedReports(): Promise<PublishedReportSummaryV1[]> {
    await delay();
    return [
      {
        report_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
        version_number: 2,
        title: "企业安环资料分析报告",
        published_at: "2026-08-21T10:00:00+00:00",
        artifact_ready: true,
      },
    ];
  }

  async getPublishedReport(reportId: string): Promise<PublishedReportDetailV1> {
    await delay();
    if (reportId !== "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa") {
      throw new ApiError(404, "REPORT_NOT_FOUND", false);
    }
    return {
      report_id: reportId,
      version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
      version_number: 2,
      title: "企业安环资料分析报告",
      published_at: "2026-08-21T10:00:00+00:00",
      artifact_ready: true,
      schema: "anhuan-analysis-report-published-detail-v1",
      sections: SYNTHETIC_SECTIONS.map((s) => ({ ...s })),
      citations: SYNTHETIC_CITATIONS.map((c) => ({ ...c })),
    };
  }

  async getPublishedHtmlArtifact(reportId: string): Promise<HtmlReportArtifact> {
    const report = await this.getPublishedReport(reportId);
    return mockHtmlArtifact(
      report,
      `MOCK-aeco-published-report-v${report.version_number}.html`,
    );
  }

  async getLatestManagementHealth(): Promise<ManagementHealthSnapshot | null> {
    await delay();
    return SYNTHETIC_MANAGEMENT_HEALTH;
  }

  // —— 运营台 · 客户 ——

  async listClients(): Promise<ClientAccount[]> {
    await delay();
    return [...this.clients];
  }

  async getClient(clientId: string): Promise<ClientAccount> {
    await delay(120);
    const found = this.clients.find((c) => c.id === clientId);
    if (!found) throw new ApiError(404, "CLIENT_NOT_FOUND", false);
    return { ...found };
  }

  async createClient(input: { name: string; stage: ClientStage }): Promise<ClientAccount> {
    await delay();
    const client: ClientAccount = {
      id: this.newId("client"),
      name: input.name,
      stage: input.stage,
      industryNote: null,
      regionNote: null,
      updatedAt: new Date().toISOString(),
      nextFollowUpAt: null,
    };
    this.clients.push(client);
    this.clientMaterials[client.id] = [];
    return { ...client };
  }

  // —— 运营台 · 材料 ——

  async listSharedMaterials(): Promise<MaterialItem[]> {
    await delay();
    return [...this.sharedMaterials];
  }

  async listClientMaterials(clientId: string): Promise<MaterialItem[]> {
    await delay();
    return [...(this.clientMaterials[clientId] ?? [])];
  }

  async uploadMaterial(input: {
    file: File;
    name: string;
    scope: "shared" | "client";
    clientId?: string;
  }): Promise<void> {
    await delay(400);
    const item: MaterialItem = {
      id: this.newId("material"),
      name: input.name || input.file.name,
      status: "processing",
      versionCount: 1,
      updatedAt: new Date().toISOString(),
    };
    if (input.scope === "shared") {
      this.sharedMaterials.push(item);
    } else if (input.clientId) {
      (this.clientMaterials[input.clientId] ??= []).push(item);
    }
  }

  // —— 运营台 · 报告工作流 ——

  private reportsOf(clientId: string): MockReport[] {
    return this.reports.filter((r) => r.clientId === clientId);
  }

  private toSummary(report: MockReport): ProviderReportSummaryV1 {
    const current = report.versions[report.versions.length - 1];
    return {
      report_id: report.reportId,
      current_version_id: current ? current.versionId : null,
      current_status: current ? current.status : "empty",
      version_number: current ? current.versionNumber : 0,
      title: "企业安环资料分析报告",
      updated_at: report.updatedAt,
    };
  }

  private findReportById(reportId: string): MockReport {
    const report = this.reports.find((r) => r.reportId === reportId);
    if (!report) throw new ApiError(404, "REPORT_NOT_FOUND", false);
    return report;
  }

  private findVersion(versionId: string): { report: MockReport; version: MockVersion } {
    for (const report of this.reports) {
      const version = report.versions.find((v) => v.versionId === versionId);
      if (version) return { report, version };
    }
    throw new ApiError(404, "REPORT_NOT_FOUND", false);
  }

  async listClientReports(clientId: string): Promise<ProviderReportSummaryV1[]> {
    await delay();
    return this.reportsOf(clientId).map((r) => this.toSummary(r));
  }

  async createReport(clientId: string, requestId: string): Promise<ProviderReportSummaryV1> {
    await delay();
    const existingId = this.createByRequest.get(`${clientId}:${requestId}`);
    if (existingId) return this.toSummary(this.findReportById(existingId));
    const report: MockReport = {
      reportId: this.newId("report"),
      clientId,
      versions: [],
      updatedAt: new Date().toISOString(),
    };
    this.reports.push(report);
    this.createByRequest.set(`${clientId}:${requestId}`, report.reportId);
    return this.toSummary(report);
  }

  async generate(
    _clientId: string,
    reportId: string,
    requestId: string,
  ): Promise<GenerationAcceptedV1> {
    await delay();
    const replay = this.generateByRequest.get(`${reportId}:${requestId}`);
    if (replay) return replay;
    const report = this.findReportById(reportId);
    const materials = this.clientMaterials[report.clientId] ?? [];
    if (materials.filter((m) => m.status === "ready").length === 0) {
      throw new ApiError(404, "REPORT_NOT_FOUND", false);
    }
    const version: MockVersion = {
      versionId: this.newId("version"),
      versionNumber: report.versions.length + 1,
      status: "generating",
      createdAt: new Date().toISOString(),
      reviewEvents: [],
    };
    report.versions.push(version);
    report.updatedAt = version.createdAt;
    const jobId = this.newId("job");
    this.jobs.set(jobId, { versionId: version.versionId, reportId: report.reportId, pollsLeft: 2 });
    const accepted: GenerationAcceptedV1 = {
      schema: "anhuan-analysis-report-generation-v1",
      job_id: jobId,
      version_id: version.versionId,
      status: "generating",
    };
    this.generateByRequest.set(`${reportId}:${requestId}`, accepted);
    return accepted;
  }

  async getJob(jobId: string): Promise<JobStatusV1> {
    const job = this.jobs.get(jobId);
    if (!job) throw new ApiError(404, "REPORT_NOT_FOUND", false);
    job.pollsLeft -= 1;
    if (job.pollsLeft <= 0) {
      const { version } = this.findVersion(job.versionId);
      version.status = "draft";
      this.jobs.delete(jobId);
      return {
        schema: "anhuan-analysis-report-job-v1",
        job_id: jobId,
        version_id: job.versionId,
        status: "draft",
        error_reason: null,
      };
    }
    return {
      schema: "anhuan-analysis-report-job-v1",
      job_id: jobId,
      version_id: job.versionId,
      status: "generating",
      error_reason: null,
    };
  }

  async getVersion(versionId: string): Promise<VersionDetailV1> {
    await delay(120);
    const { report, version } = this.findVersion(versionId);
    if (version.status === "queued" || version.status === "generating") {
      return {
        schema: "anhuan-analysis-report-draft-v1",
        report_id: report.reportId,
        version_id: version.versionId,
        version_number: version.versionNumber,
        status: version.status,
        title: "企业安环资料分析报告",
        sections: [],
        citations: [],
        review_events: version.reviewEvents.map((event) => ({
          ...event,
          checklist: { ...event.checklist },
        })),
      };
    }
    return {
      schema: "anhuan-analysis-report-draft-v1",
      report_id: report.reportId,
      version_id: version.versionId,
      version_number: version.versionNumber,
      status: version.status,
      title: "企业安环资料分析报告",
      sections: SYNTHETIC_SECTIONS.map((s) => ({ ...s })),
      citations: SYNTHETIC_CITATIONS.map((c) => ({ ...c })),
      review_events: version.reviewEvents.map((event) => ({
        ...event,
        checklist: { ...event.checklist },
      })),
    };
  }

  async getVersionHtmlArtifact(versionId: string): Promise<HtmlReportArtifact> {
    const report = await this.getVersion(versionId);
    if (report.sections.length === 0) {
      throw new ApiError(409, "MOCK_REPORT_ARTIFACT_NOT_READY", false);
    }
    return mockHtmlArtifact(
      report,
      `MOCK-aeco-analysis-report-v${report.version_number}.html`,
    );
  }

  async listVersions(reportId: string): Promise<VersionHistoryItemV1[]> {
    await delay(120);
    const report = this.findReportById(reportId);
    return report.versions.map((v) => ({
      version_id: v.versionId,
      version_number: v.versionNumber,
      status: v.status,
      created_at: v.createdAt,
    }));
  }

  async transition(
    versionId: string,
    action: TransitionAction,
    evidence?: TransitionEvidence,
  ): Promise<ProviderReportSummaryV1> {
    await delay();
    const reviewEvidence = normalizeTransitionEvidence(action, evidence);
    const { report, version } = this.findVersion(versionId);
    const legal: Record<TransitionAction, ReportStatus[]> = {
      submit: ["draft"],
      return: ["review_pending"],
      approve: ["review_pending"],
      publish: ["approved"],
      withdraw: ["published"],
    };
    if (!legal[action].includes(version.status)) {
      throw new ApiError(409, "REPORT_TRANSITION_INVALID", false);
    }
    const next: Record<TransitionAction, ReportStatus> = {
      submit: "review_pending",
      return: "changes_requested",
      approve: "approved",
      publish: "published",
      withdraw: "withdrawn",
    };
    if (action === "publish") {
      for (const v of report.versions) {
        if (v.status === "published") v.status = "superseded";
      }
    }
    version.status = next[action];
    report.updatedAt = new Date().toISOString();
    if (action === "submit" || action === "return" || action === "approve") {
      version.reviewEvents.push({
        event_id: crypto.randomUUID(),
        action,
        checklist: { ...(reviewEvidence?.checklist ?? {}) },
        comment: reviewEvidence?.comment ?? null,
        created_at: report.updatedAt,
      });
    }
    return this.toSummary(report);
  }

  // —— 运营台 · 异常中心 ——

  async listExceptions(): Promise<ExceptionItem[]> {
    await delay();
    return [...this.exceptions];
  }
}
