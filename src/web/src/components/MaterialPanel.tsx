// 材料入库面板（共享域 / 客户域通用，两域从不聚合）。
// 正式 HTTP：完整复用 P3 受控入库能力——状态只来自后端可证明的上传/扫描/隔离/解析字段；
// 动作只消费 capabilities 与 allowed_actions；按钮在无权限或扫描不可用时隐藏。
// 入库完成不代表可生成报告，生成资格由后端在生成时校验（前端不推导）。
// 演示环境（mock）：保留简表走查，不伪造生命周期。
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { Link } from "react-router-dom";
import { isMockData, useApi } from "../adapters";
import { ApiError } from "../adapters/errors";
import type { MaterialItem, MaterialStatus } from "../adapters/types";
import { MATERIAL_STATUS_LABEL } from "../adapters/types";
import { useAuth } from "../auth/OidcProvider";
import { useNarrow } from "../pages/console/useNarrow";
import {
  createIngestionDocument,
  getAutoPipelineStatus,
  getIngestionCapabilities,
  getIngestionDocument,
  getMaterialIntakeAnalysis,
  getPageTextUnit,
  getPreviewImageUnitBlob,
  getPreviewManifest,
  listIngestionDocuments,
  processIngestionVersion,
  releaseIngestionVersion,
  rejectIngestionVersion,
  retryIngestionVersion,
  setMaterialIntakeClassification,
  userFacingIngestionError,
} from "../features/p3/ingestionApi";
import DocumentUploadModal from "../features/p3/components/DocumentUploadModal";
import { formatBytes, reasonCopy } from "../features/p3/reasonCopy";
import type {
  AutoPipelineStage,
  AutoPipelineStageStatus,
  AutoPipelineStatus,
  DocumentCollection,
  DocumentSummary,
  IngestionCapabilities,
  MaterialFieldCandidate,
  MaterialIntakeAnalysis,
  MaterialKind,
  MaterialPageClassification,
  VersionSummary,
} from "../features/p3/types";
import ErrorState from "./ErrorState";
import StatusDot, { type StatusTone } from "./StatusDot";
import { formatDateTime } from "./ReportDocument";

const MATERIAL_KIND_LABEL: Record<MaterialKind, string> = {
  policy: "制度文件",
  report: "报告",
  unknown: "未分类",
};

const MATERIAL_FIELD_LABEL: Record<string, string> = {
  source_title: "来源名称",
  publisher: "发布主体",
  source_type: "来源类型",
  jurisdiction: "地区／层级",
  source_reference: "内部来源引用",
  version_title: "版本标题",
  domain: "领域",
  effect_status: "效力状态",
  issued_on: "颁布日期",
  effective_from: "生效起日",
  effective_to: "生效止日",
  summary: "候选摘要",
  report_title: "报告标题",
  report_date: "报告日期",
  report_summary: "报告摘要",
};

function pageKindLabel(kind: string): string {
  if (kind === "text") return "文本型";
  if (kind === "scanned") return "扫描型";
  if (kind === "mixed") return "混合型";
  if (kind === "table") return "表格型";
  if (kind === "two_column") return "双栏型";
  return "待判断";
}

function confidenceLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${(Math.max(0, Math.min(1_000_000, value)) / 10_000).toFixed(1)}% 机器线索`;
}

type OcrOutcome = "applied" | "unavailable" | "required" | "native";

function ocrOutcomeOf(page: MaterialPageClassification): OcrOutcome {
  if (page.reason_codes.includes("OCR_APPLIED")) return "applied";
  if (page.reason_codes.includes("OCR_UNAVAILABLE")) return "unavailable";
  if (page.ocr_required || page.reason_codes.includes("OCR_REQUIRED")) return "required";
  return "native";
}

function ocrOutcomeTag(page: MaterialPageClassification) {
  const outcome = ocrOutcomeOf(page);
  if (outcome === "applied") return <Tag color="green">已 OCR</Tag>;
  if (outcome === "unavailable") return <Tag color="red">OCR 不可用</Tag>;
  if (outcome === "required") return <Tag color="gold">需要 OCR</Tag>;
  return <Tag>原生文本</Tag>;
}

function pdfCapabilityOf(capabilities: IngestionCapabilities | null) {
  return capabilities?.allowed_types.find((item) => item.content_type === "application/pdf") ?? null;
}

function validatePdfFile(file: File, capabilities: IngestionCapabilities | null): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "请选择 PDF 文件";
  if (file.size <= 0) return reasonCopy("EMPTY_FILE");
  if (!capabilities) return null;
  const pdfCapability = pdfCapabilityOf(capabilities);
  if (!pdfCapability) return reasonCopy("FILE_TYPE_NOT_ALLOWED");
  const maxBytes = Math.min(pdfCapability.max_file_bytes, capabilities.limits.max_file_bytes);
  if (file.size > maxBytes) return `${reasonCopy("FILE_TOO_LARGE")}（上限 ${formatBytes(maxBytes)}）`;
  return null;
}

// 内部状态 → 业务阶段。只映射后端可证明字段；入库完成≠具备生成资格。
type BusinessStage =
  | "等待处理"
  | "安全检查"
  | "文字识别与解析"
  | "待确认"
  | "入库处理完成"
  | "处理失败"
  | "已停用";

function businessStageOf(version: VersionSummary | null): BusinessStage {
  if (!version) return "等待处理";
  if (version.workflow_status === "failed") return "处理失败";
  if (version.workflow_status === "blocked") return "待确认";
  if (version.scan_status === "infected" || version.scan_status === "error") {
    return "处理失败";
  }
  if (version.preview_status === "failed") return "处理失败";
  if (version.quarantine_status === "blocked") return "已停用";
  if (
    version.scan_status === "queued" ||
    version.scan_status === "scanning" ||
    version.scan_status === "unavailable"
  ) {
    return "安全检查";
  }
  if (
    version.workflow_status === "processing" ||
    version.preview_status === "queued" ||
    version.preview_status === "generating"
  ) {
    return "文字识别与解析";
  }
  if (
    version.workflow_status === "ready" &&
    version.quarantine_status === "released" &&
    version.scan_status === "clean"
  ) {
    return "入库处理完成";
  }
  // blocked 等其余状态：待确认，原因由 reason_code 说明
  return "待确认";
}

const STAGE_TONE: Record<BusinessStage, StatusTone> = {
  等待处理: "neutral",
  安全检查: "processing",
  文字识别与解析: "processing",
  待确认: "warning",
  入库处理完成: "success",
  处理失败: "danger",
  已停用: "neutral",
};

const ACTIVE_STAGES: ReadonlySet<BusinessStage> = new Set([
  "等待处理",
  "安全检查",
  "文字识别与解析",
]);

function stageOf(doc: DocumentSummary): BusinessStage {
  return businessStageOf(doc.latest_version);
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const anyError = error as { status?: number; code?: string; retryable?: boolean };
  return new ApiError(
    typeof anyError?.status === "number" ? anyError.status : 0,
    typeof anyError?.code === "string" ? anyError.code : "NETWORK_ERROR",
    Boolean(anyError?.retryable),
  );
}

// 响应复核：混入其他域/客户即 fail-closed
function assertScopePure(
  collection: DocumentCollection,
  scope: "shared" | "client",
  clientId?: string,
): void {
  const wantKind = scope === "shared" ? "service_provider" : "client";
  for (const item of collection.items) {
    if (item.knowledge_scope?.kind !== wantKind) {
      throw new ApiError(409, "SCOPE_MIXED", false);
    }
    if (
      scope === "client" &&
      clientId &&
      item.knowledge_scope.client_account_id !== clientId
    ) {
      throw new ApiError(409, "SCOPE_MIXED", false);
    }
  }
}

// —— 版本动作（只消费服务端 allowed_actions） ——

const VERSION_ACTIONS: Array<{
  action: "process" | "retry" | "release" | "reject";
  label: string;
  danger?: boolean;
}> = [
  { action: "process", label: "开始处理" },
  { action: "retry", label: "重试" },
  { action: "release", label: "确认可用" },
  { action: "reject", label: "停用", danger: true },
];

function VersionActions({
  version,
  onDone,
}: {
  version: VersionSummary;
  onDone: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (action: "process" | "retry" | "release" | "reject") => {
    setBusy(action);
    try {
      const fn = {
        process: processIngestionVersion,
        retry: retryIngestionVersion,
        release: releaseIngestionVersion,
        reject: rejectIngestionVersion,
      }[action];
      await fn(getAccessToken(), version.id);
      message.success(action === "reject" ? "已停用该版本" : "操作已提交");
      onDone();
    } catch (e) {
      message.error(userFacingIngestionError(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
      {VERSION_ACTIONS.filter((a) => version.allowed_actions.includes(a.action)).map((a) => (
        <Button
          key={a.action}
          size="small"
          danger={a.danger}
          loading={busy === a.action}
          onClick={() => void run(a.action)}
        >
          {a.label}
        </Button>
      ))}
    </span>
  );
}

// —— 安全预览（page_text 行 / image blob；其余诚实说明） ——

function VersionPreview({ versionId }: { versionId: string }) {
  const { getAccessToken } = useAuth();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "text"; lines: string[] }
    | { kind: "image"; url: string }
    | { kind: "unavailable"; reason: string }
  >({ kind: "loading" });

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setState({ kind: "loading" });
    (async () => {
      try {
        const manifest = await getPreviewManifest(getAccessToken(), versionId);
        if (manifest.status !== "ready" || manifest.units.length === 0) {
          if (active) {
            setState({
              kind: "unavailable",
              reason:
                manifest.status === "generating" || manifest.status === "blocked"
                  ? "安全预览生成中"
                  : reasonCopy(manifest.reason_code ?? "PREVIEW_FAILED"),
            });
          }
          return;
        }
        const unit = manifest.units[0];
        if (unit.kind === "page_text") {
          const page = await getPageTextUnit(getAccessToken(), versionId, unit.id);
          if (active) setState({ kind: "text", lines: page.lines.slice(0, 12) });
        } else if (unit.kind === "image") {
          const blob = await getPreviewImageUnitBlob(getAccessToken(), versionId, unit.id);
          objectUrl = URL.createObjectURL(blob);
          if (active) setState({ kind: "image", url: objectUrl });
        } else {
          if (active) setState({ kind: "unavailable", reason: "表格文件暂不支持页内预览" });
        }
      } catch (e) {
        if (active) setState({ kind: "unavailable", reason: userFacingIngestionError(e) });
      }
    })();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [getAccessToken, versionId]);

  if (state.kind === "loading") return <Spin size="small" />;
  if (state.kind === "unavailable") {
    return <Typography.Text type="secondary">{state.reason}</Typography.Text>;
  }
  if (state.kind === "image") {
    return (
      <img
        src={state.url}
        alt="材料首页安全预览"
        style={{ maxWidth: "100%", border: "1px solid var(--eco-border)" }}
      />
    );
  }
  return (
    <div
      style={{
        border: "1px solid var(--eco-border)",
        padding: 12,
        fontSize: 13,
        lineHeight: 1.8,
        maxHeight: 240,
        overflow: "auto",
      }}
    >
      {state.lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

// —— PDF 自动处理与机器分析（只展示后端已返回的事实） ——

type AnalysisLoadState = "waiting" | "loading" | "ready" | "missing" | "failed";
type PipelineLoadState = "loading" | "ready" | "failed";
type PipelineStageKey = "ingestion" | "analysis" | "index" | "report";

const PIPELINE_STATUS_LABEL: Record<AutoPipelineStageStatus, string> = {
  disabled: "未启用",
  pending: "等待中",
  running: "处理中",
  ready: "已完成",
  failed: "失败",
  skipped: "已跳过",
};

const PIPELINE_STATUS_TONE: Record<AutoPipelineStageStatus, StatusTone> = {
  disabled: "neutral",
  pending: "warning",
  running: "processing",
  ready: "success",
  failed: "danger",
  skipped: "neutral",
};

const POLLABLE_PENDING_REASONS = new Set([
  "INGESTION_PROCESSING",
  "INGESTION_RETRY_WAIT",
  "MATERIAL_INGESTION_DELIVERY_FAILED",
  "MATERIAL_INGESTION_QUEUE_UNAVAILABLE",
  "MATERIAL_ANALYSIS_PENDING",
  "MATERIAL_INDEX_PENDING",
  "REPORT_WAITING_FOR_INDEX",
  "REPORT_GENERATION_QUEUED",
  "REPORT_GENERATION_PENDING",
]);

const REPORT_SOURCE_REASONS = new Set([
  "REPORT_CLIENT_SOURCES_EMPTY",
  "REPORT_SOURCES_INCOMPLETE",
]);

function pipelineStageStatus(key: PipelineStageKey, stage: AutoPipelineStage): string {
  if (key === "report" && stage.reason_code === "REPORT_REVIEW_REQUIRED") return "待审核";
  if (key === "report" && stage.status === "ready") return "草稿已生成";
  if (key === "report" && REPORT_SOURCE_REASONS.has(stage.reason_code ?? "")) return "待补资料";
  if (key === "analysis" && stage.reason_code === "OCR_REQUIRED") return "需要 OCR";
  return PIPELINE_STATUS_LABEL[stage.status];
}

function pipelineStageTone(key: PipelineStageKey, stage: AutoPipelineStage): StatusTone {
  if (key === "report" && stage.reason_code === "REPORT_REVIEW_REQUIRED") return "warning";
  if (key === "analysis" && stage.reason_code === "OCR_REQUIRED") return "warning";
  return PIPELINE_STATUS_TONE[stage.status];
}

function pipelineStageDetail(key: PipelineStageKey, stage: AutoPipelineStage): string {
  if (key === "report" && stage.reason_code === "REPORT_REVIEW_REQUIRED") {
    return "报告草稿正在等待复核；审核通过后仍需发布。";
  }
  if (key === "report" && stage.status === "ready") {
    return "报告草稿已生成；这不代表已审核或已发布。";
  }
  if (stage.reason_code) return reasonCopy(stage.reason_code);
  if (stage.status === "ready") return "后端已确认该阶段完成。";
  if (stage.status === "running") return "后端任务正在处理。";
  if (stage.status === "pending") return "正在等待前置条件或任务调度。";
  if (stage.status === "disabled") return "当前环境未启用该阶段。";
  if (stage.status === "skipped") return "该阶段不适用，未执行。";
  return "后端返回该阶段失败。";
}

function PipelineLine({
  label,
  status,
  tone,
  detail,
  nextStep,
}: {
  label: string;
  status: string;
  tone: StatusTone;
  detail: string;
  nextStep?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
        alignItems: "start",
        padding: "10px 0",
        borderTop: "1px solid var(--eco-border)",
      }}
    >
      <Typography.Text strong style={{ width: 100, flex: "0 0 100px" }}>{label}</Typography.Text>
      <span style={{ width: 116, flex: "0 0 116px" }}>
        <StatusDot tone={tone} label={status} />
      </span>
      <Typography.Text type="secondary" style={{ fontSize: 13, flex: "1 1 220px" }}>
        {detail}
      </Typography.Text>
      <span style={{ minWidth: 96, flex: "0 0 auto" }}>
        <Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>下一步</Typography.Text>
        {nextStep ?? <Typography.Text type="secondary" style={{ fontSize: 13 }}>无需操作</Typography.Text>}
      </span>
    </div>
  );
}

function MaterialPipelineSection({
  version,
  analysis,
  analysisState,
  pipeline,
  pipelineState,
  pipelineError,
  pipelineActive,
  clientAccountId,
  onRetry,
  canReprocess,
  reprocessing,
  onReprocess,
  onRequestUpload,
}: {
  version: VersionSummary;
  analysis: MaterialIntakeAnalysis | null;
  analysisState: AnalysisLoadState;
  pipeline: AutoPipelineStatus | null;
  pipelineState: PipelineLoadState;
  pipelineError: string | null;
  pipelineActive: boolean;
  clientAccountId?: string;
  onRetry: () => void;
  canReprocess: boolean;
  reprocessing: boolean;
  onReprocess: () => void;
  onRequestUpload?: () => void;
}) {
  const isPdf = version.content_type === "application/pdf";
  const ocrAppliedPages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "applied").length ?? 0;
  const ocrUnavailablePages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "unavailable").length ?? 0;
  const ocrRequiredPages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "required").length ?? 0;

  let safety: { status: string; tone: StatusTone; detail: string };
  if (version.scan_status === "clean") {
    safety = { status: "扫描通过", tone: "success", detail: "已完成本地安全扫描。" };
  } else if (["queued", "scanning", "unavailable"].includes(version.scan_status)) {
    safety = { status: "处理中", tone: "processing", detail: "文件仍在隔离区，等待安全扫描结果。" };
  } else {
    safety = {
      status: "未通过",
      tone: "danger",
      detail: reasonCopy(version.reason_code ?? "MALWARE_DETECTED"),
    };
  }

  let parsing: { status: string; tone: StatusTone; detail: string };
  if (!isPdf) {
    parsing = { status: "不适用", tone: "neutral", detail: "当前版本不是 PDF。" };
  } else if (version.scan_status !== "clean") {
    parsing = {
      status: ["queued", "scanning", "unavailable"].includes(version.scan_status) ? "等待安全检查" : "未进入解析",
      tone: ["queued", "scanning", "unavailable"].includes(version.scan_status) ? "processing" : "danger",
      detail: "PDF 只有在安全扫描通过后才会进入解析。",
    };
  } else if (version.preview_status === "failed" || version.preview_status === "blocked") {
    parsing = {
      status: "未生成分析",
      tone: "danger",
      detail: reasonCopy(version.reason_code ?? "PREVIEW_FAILED"),
    };
  } else if (["queued", "generating"].includes(version.preview_status)) {
    parsing = {
      status: "等待预览",
      tone: "processing",
      detail: "安全预览完成后，才会返回 PDF 机器分析。",
    };
  } else if (analysisState === "waiting" || analysisState === "loading") {
    parsing = {
      status: analysisState === "loading" ? "读取中" : "等待解析",
      tone: "processing",
      detail: "安全扫描和预览完成后，才会返回 PDF 机器分析。",
    };
  } else if (analysisState === "failed" || analysis?.status === "failed") {
    parsing = {
      status: "分析失败",
      tone: "danger",
      detail: analysis?.reason_code ? reasonCopy(analysis.reason_code) : "暂时无法读取材料分析。",
    };
  } else if (analysisState === "missing" || !analysis) {
    parsing = {
      status: "未生成",
      tone: "warning",
      detail: "安全预览可能可用，但当前版本没有材料分析记录。",
    };
  } else if (ocrUnavailablePages > 0) {
    parsing = {
      status: "OCR 不完整",
      tone: "danger",
      detail: `${ocrUnavailablePages} 页返回 OCR 不可用；请以页级状态和证据为准。`,
    };
  } else if (ocrRequiredPages > 0) {
    parsing = {
      status: "需要 OCR",
      tone: "warning",
      detail: `${ocrRequiredPages} 页被标记为需要 OCR，尚未返回 OCR_APPLIED 结果。`,
    };
  } else {
    parsing = {
      status: ocrAppliedPages > 0 ? "解析与 OCR 完成" : "解析完成",
      tone: "success",
      detail: ocrAppliedPages > 0
        ? `${analysis.page_count} 页·${analysis.candidate_count} 个字段候选，其中 ${ocrAppliedPages} 页已 OCR。`
        : `${analysis.page_count} 页·${analysis.candidate_count} 个字段候选，当前均为原生文本。`,
    };
  }

  const reportsPath = clientAccountId
    ? `/console/clients/${encodeURIComponent(clientAccountId)}/reports`
    : null;

  const nextStepFor = (key: PipelineStageKey, stage: AutoPipelineStage): ReactNode => {
    if (key === "report" && reportsPath && stage.reason_code === "REPORT_REVIEW_REQUIRED") {
      return <Link to={reportsPath}>去审核</Link>;
    }
    if (key === "report" && reportsPath && stage.status === "ready") {
      return <Link to={reportsPath}>查看报告草稿</Link>;
    }
    if (key === "report" && REPORT_SOURCE_REASONS.has(stage.reason_code ?? "") && onRequestUpload) {
      return <Button type="link" size="small" style={{ padding: 0 }} onClick={onRequestUpload}>补充客户材料</Button>;
    }
    if (key === "report" && REPORT_SOURCE_REASONS.has(stage.reason_code ?? "")) {
      return <Typography.Text type="secondary" style={{ fontSize: 13 }}>当前账号不可上传</Typography.Text>;
    }
    if (key === "report" && stage.reason_code === "REPORT_PROVIDER_SOURCES_MISSING") {
      return <Link to="/console/shared-materials">补充共享材料</Link>;
    }
    if (key === "report" && stage.reason_code === "REPORT_CLIENT_SCOPE_REQUIRED") {
      return <Link to="/console/clients">选择客户</Link>;
    }
    if (
      key === "report" &&
      reportsPath &&
      ["REPORT_CLIENT_BINDING_REQUIRED", "REPORT_GENERATION_FAILED"].includes(stage.reason_code ?? "")
    ) {
      return <Link to={reportsPath}>查看报告列表</Link>;
    }
    if (
      stage.status === "running" ||
      (stage.status === "pending" && POLLABLE_PENDING_REASONS.has(stage.reason_code ?? ""))
    ) {
      return <Typography.Text type="secondary" style={{ fontSize: 13 }}>自动刷新中</Typography.Text>;
    }
    if (
      canReprocess &&
      (stage.status === "failed" || stage.status === "pending")
    ) {
      return <Button type="link" size="small" style={{ padding: 0 }} loading={reprocessing} onClick={onReprocess}>重新处理</Button>;
    }
    if (stage.status === "failed") {
      return <Button type="link" size="small" style={{ padding: 0 }} onClick={onRetry}>重试读取</Button>;
    }
    if (stage.status === "pending") {
      return <Button type="link" size="small" style={{ padding: 0 }} onClick={onRetry}>刷新状态</Button>;
    }
    return <Typography.Text type="secondary" style={{ fontSize: 13 }}>无需操作</Typography.Text>;
  };

  return (
    <section aria-labelledby={`material-pipeline-${version.id}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 16 }}>
        <Typography.Title id={`material-pipeline-${version.id}`} level={5} style={{ marginBottom: 4 }}>
          自动处理流水线
        </Typography.Title>
        <Button size="small" loading={pipelineState === "loading"} onClick={onRetry}>
          刷新状态
        </Button>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {pipeline
          ? pipelineActive
            ? "后续阶段正在处理，详情每 3 秒自动刷新。"
            : `自动执行${pipeline.enabled ? "已启用" : "未启用"}；各阶段状态来自后端流水线回执。`
          : "这里只显示当前接口能证明的状态；未取得回执时不猜测索引或报告结果。"}
      </Typography.Paragraph>
      {pipelineState === "failed" && (
        <Alert
          type="warning"
          showIcon
          message="流水线状态暂不可用"
          description={pipelineError ?? "未取得后端流水线回执。"}
          action={<Button size="small" onClick={onRetry}>立即重试</Button>}
          style={{ marginBottom: 8 }}
        />
      )}
      <div aria-live="polite">
        {pipeline ? (
          <>
            {([
              ["ingestion", "安全入库", pipeline.ingestion],
              ["analysis", "PDF 解析／OCR", pipeline.analysis],
              ["index", "知识索引", pipeline.index],
              ["report", "分析报告", pipeline.report],
            ] as const).map(([key, label, stage]) => (
              <PipelineLine
                key={key}
                label={label}
                status={pipelineStageStatus(key, stage)}
                tone={pipelineStageTone(key, stage)}
                detail={pipelineStageDetail(key, stage)}
                nextStep={nextStepFor(key, stage)}
              />
            ))}
          </>
        ) : (
          <>
            <PipelineLine label="安全入库" {...safety} />
            <PipelineLine label="PDF 解析／OCR" {...parsing} />
            <PipelineLine
              label="知识索引"
              status={pipelineState === "loading" ? "读取中" : "状态不可用"}
              tone={pipelineState === "loading" ? "processing" : "neutral"}
              detail="未取得后端索引阶段回执，不推导完成状态。"
              nextStep={pipelineState === "loading" ? "读取中" : <Button type="link" size="small" style={{ padding: 0 }} onClick={onRetry}>重试</Button>}
            />
            <PipelineLine
              label="分析报告"
              status={pipelineState === "loading" ? "读取中" : "状态不可用"}
              tone={pipelineState === "loading" ? "processing" : "neutral"}
              detail="未取得后端报告阶段回执，不推导报告已生成。"
              nextStep={pipelineState === "loading" ? "读取中" : <Button type="link" size="small" style={{ padding: 0 }} onClick={onRetry}>重试</Button>}
            />
          </>
        )}
      </div>
    </section>
  );
}

function MaterialAnalysisSection({
  version,
  clientAccountId,
  onDone,
  onRequestUpload,
}: {
  version: VersionSummary;
  clientAccountId?: string;
  onDone: () => void;
  onRequestUpload?: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [analysis, setAnalysis] = useState<MaterialIntakeAnalysis | null>(null);
  const [state, setState] = useState<AnalysisLoadState>("waiting");
  const [error, setError] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<AutoPipelineStatus | null>(null);
  const [pipelineState, setPipelineState] = useState<PipelineLoadState>("loading");
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const [pipelineNonce, setPipelineNonce] = useState(0);
  const [analysisNonce, setAnalysisNonce] = useState(0);
  const [kind, setKind] = useState<MaterialKind>("unknown");
  const [saving, setSaving] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const isPdf = version.content_type === "application/pdf";
  const canRead = isPdf && version.scan_status === "clean" && version.preview_status === "ready";
  const ocrAppliedPages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "applied").length ?? 0;
  const ocrRequiredPages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "required").length ?? 0;
  const ocrUnavailablePages = analysis?.pages.filter((page) => ocrOutcomeOf(page) === "unavailable").length ?? 0;

  useEffect(() => {
    setPipeline(null);
    setPipelineState("loading");
    setPipelineError(null);
  }, [version.id]);

  useEffect(() => {
    const controller = new AbortController();
    setPipelineState("loading");
    setPipelineError(null);
    getAutoPipelineStatus(getAccessToken(), version.id, clientAccountId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (
          result.schema !== "anhuan-material-auto-pipeline-v1" ||
          result.version_id !== version.id
        ) {
          setPipeline(null);
          setPipelineState("failed");
          setPipelineError("后端返回的流水线回执与当前版本不匹配。");
          return;
        }
        setPipeline(result);
        setPipelineState("ready");
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        setPipeline(null);
        setPipelineState("failed");
        setPipelineError(userFacingIngestionError(requestError));
      });
    return () => controller.abort();
  }, [clientAccountId, getAccessToken, pipelineNonce, version.id, version.updated_at]);

  const pipelineActive = pipeline
    ? [pipeline.ingestion, pipeline.analysis, pipeline.index, pipeline.report].some(
        (stage) =>
          stage.status === "running" ||
          (stage.status === "pending" &&
            stage.reason_code !== null &&
            POLLABLE_PENDING_REASONS.has(stage.reason_code)),
      )
    : false;

  useEffect(() => {
    if (!pipeline?.enabled || !pipelineActive) return;
    const timer = window.setTimeout(() => setPipelineNonce((value) => value + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [pipeline, pipelineActive]);

  useEffect(() => {
    if (pipelineState !== "failed") return;
    const timer = window.setTimeout(() => setPipelineNonce((value) => value + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [pipelineState]);

  useEffect(() => {
    const controller = new AbortController();
    setAnalysis(null);
    setError(null);
    setKind("unknown");
    if (!canRead) {
      setState("waiting");
      return () => controller.abort();
    }
    setState("loading");
    getMaterialIntakeAnalysis(getAccessToken(), version.id, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setAnalysis(result);
        setKind(result.resolved_kind);
        setState("ready");
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        const status = (requestError as { status?: number }).status;
        setState(status === 404 ? "missing" : "failed");
        setError(status === 404 ? null : userFacingIngestionError(requestError));
      });
    return () => controller.abort();
  }, [analysisNonce, canRead, getAccessToken, pipeline?.analysis.status, version.id, version.updated_at]);

  const saveClassification = async () => {
    if (!analysis?.allowed_actions.includes("set_material_kind")) return;
    setSaving(true);
    try {
      const updated = await setMaterialIntakeClassification(
        getAccessToken(),
        analysis.id,
        kind,
      );
      setAnalysis(updated);
      setKind(updated.resolved_kind);
      setPipelineNonce((value) => value + 1);
      message.success("分类已保存");
      onDone();
    } catch (requestError) {
      message.error(userFacingIngestionError(requestError));
    } finally {
      setSaving(false);
    }
  };

  const canReprocess =
    version.allowed_actions.includes("process") || version.allowed_actions.includes("retry");

  const reprocess = async () => {
    if (!canReprocess) return;
    setReprocessing(true);
    try {
      const request = version.allowed_actions.includes("process")
        ? processIngestionVersion
        : retryIngestionVersion;
      await request(getAccessToken(), version.id);
      message.success("已提交重新处理");
      setPipelineNonce((value) => value + 1);
      setAnalysisNonce((value) => value + 1);
      onDone();
    } catch (requestError) {
      message.error(userFacingIngestionError(requestError));
    } finally {
      setReprocessing(false);
    }
  };

  return (
    <>
      <MaterialPipelineSection
        version={version}
        analysis={analysis}
        analysisState={state}
        pipeline={pipeline}
        pipelineState={pipelineState}
        pipelineError={pipelineError}
        pipelineActive={pipelineActive}
        clientAccountId={clientAccountId}
        onRetry={() => setPipelineNonce((value) => value + 1)}
        canReprocess={canReprocess}
        reprocessing={reprocessing}
        onReprocess={() => void reprocess()}
        onRequestUpload={onRequestUpload}
      />
      <section aria-labelledby={`material-analysis-${version.id}`}>
        <Typography.Title id={`material-analysis-${version.id}`} level={5} style={{ marginBottom: 4 }}>
          PDF 分析结果
        </Typography.Title>
        {!isPdf ? (
          <Typography.Text type="secondary">当前版本不是 PDF，无页级分析结果。</Typography.Text>
        ) : !canRead ? (
          <Typography.Text type="secondary">
            等待安全扫描和预览完成后读取机器分析。
          </Typography.Text>
        ) : state === "loading" ? (
          <Spin size="small" />
        ) : state === "missing" ? (
          <Alert
            type="info"
            showIcon
            message="尚无材料分析"
            description="安全预览可能仍可用，但当前版本没有页级分析和字段候选记录。"
            action={<Button size="small" onClick={() => setAnalysisNonce((value) => value + 1)}>重新读取</Button>}
          />
        ) : state === "failed" || !analysis ? (
          <Alert
            type="warning"
            showIcon
            message="材料分析暂不可用"
            description={error ?? reasonCopy(analysis?.reason_code)}
            action={<Button size="small" onClick={() => setAnalysisNonce((value) => value + 1)}>重试</Button>}
          />
        ) : analysis.status === "failed" ? (
          <Alert
            type="warning"
            showIcon
            message="机器分析未完成"
            description={reasonCopy(analysis.reason_code)}
            action={<Button size="small" onClick={() => setAnalysisNonce((value) => value + 1)}>刷新结果</Button>}
          />
        ) : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                gap: "10px 20px",
                padding: "12px 0",
                borderTop: "1px solid var(--eco-border)",
                borderBottom: "1px solid var(--eco-border)",
              }}
            >
              <div><Typography.Text type="secondary">文档类型</Typography.Text><br /><Typography.Text strong>{pageKindLabel(analysis.document_profile)} PDF</Typography.Text></div>
              <div><Typography.Text type="secondary">解析引擎</Typography.Text><br /><Typography.Text>{analysis.parser_backend}</Typography.Text></div>
              <div><Typography.Text type="secondary">页数</Typography.Text><br /><Typography.Text>{analysis.page_count}</Typography.Text></div>
              <div><Typography.Text type="secondary">OCR 结果</Typography.Text><br /><Typography.Text>{ocrAppliedPages} 页已完成 · {ocrRequiredPages} 页待处理 · {ocrUnavailablePages} 页不可用</Typography.Text></div>
              <div><Typography.Text type="secondary">PDF Inspector</Typography.Text><br /><Typography.Text>{analysis.shadow_status === "disabled" ? "未启用" : analysis.shadow_status}</Typography.Text></div>
            </div>

            <div>
              <Typography.Text strong>材料分类</Typography.Text>
              <div style={{ marginTop: 8 }}>
                {analysis.allowed_actions.includes("set_material_kind") ? (
                  <Space wrap>
                    <Select<MaterialKind>
                      size="small"
                      value={kind}
                      style={{ width: 160 }}
                      options={(Object.keys(MATERIAL_KIND_LABEL) as MaterialKind[]).map((value) => ({
                        value,
                        label: MATERIAL_KIND_LABEL[value],
                      }))}
                      onChange={setKind}
                    />
                    <Button size="small" loading={saving} onClick={() => void saveClassification()}>
                      保存分类
                    </Button>
                    <Typography.Text type="secondary">
                      机器建议：{MATERIAL_KIND_LABEL[analysis.suggested_kind]}，{confidenceLabel(analysis.suggested_kind_confidence_ppm)}
                    </Typography.Text>
                  </Space>
                ) : (
                  <Typography.Text type="secondary">
                    {MATERIAL_KIND_LABEL[analysis.resolved_kind]}（当前状态不允许修改）
                  </Typography.Text>
                )}
              </div>
            </div>

            <div>
              <Typography.Text strong>逐页分析与 OCR 状态</Typography.Text>
              <Table<MaterialPageClassification>
                rowKey="page_number"
                size="small"
                pagination={false}
                dataSource={analysis.pages}
                scroll={{ x: 650 }}
                style={{ marginTop: 8 }}
                columns={[
                  { title: "页码", dataIndex: "page_number", width: 70 },
                  {
                    title: "页面类型",
                    dataIndex: "primary_kind",
                    width: 100,
                    render: pageKindLabel,
                  },
                  {
                    title: "OCR",
                    dataIndex: "ocr_required",
                    width: 120,
                    render: (_, page) => ocrOutcomeTag(page),
                  },
                  {
                    title: "版式线索",
                    key: "layout",
                    width: 180,
                    render: (_, page) => [
                      page.table_candidate ? "表格" : null,
                      page.two_column_candidate ? "双栏" : null,
                    ].filter(Boolean).join("·") || "普通版式",
                  },
                  { title: "文本字符", dataIndex: "text_character_count", width: 100 },
                  {
                    title: "分类线索",
                    key: "confidence",
                    width: 150,
                    render: (_, page) => confidenceLabel(
                      page.primary_kind === "scanned" ? page.scan_confidence_ppm : page.text_confidence_ppm,
                    ),
                  },
                ]}
              />
            </div>

            <div>
              <Typography.Text strong>字段候选与页码证据</Typography.Text>
              {analysis.candidates.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有达到保留条件的字段候选" />
              ) : (
                <Table<MaterialFieldCandidate>
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={analysis.candidates}
                  scroll={{ x: 780 }}
                  style={{ marginTop: 8 }}
                  columns={[
                    {
                      title: "候选字段",
                      dataIndex: "field_name",
                      width: 120,
                      render: (value: string) => MATERIAL_FIELD_LABEL[value] ?? value,
                    },
                    {
                      title: "机器草稿",
                      dataIndex: "candidate_value",
                      width: 210,
                      render: (value: string) => <Typography.Text style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{value}</Typography.Text>,
                    },
                    { title: "页码", dataIndex: "page_number", width: 70 },
                    {
                      title: "证据片段",
                      dataIndex: "evidence_snippet",
                      width: 260,
                      render: (value: string) => <Typography.Text type="secondary" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{value || "—"}</Typography.Text>,
                    },
                    {
                      title: "置信线索",
                      dataIndex: "confidence_ppm",
                      width: 190,
                      render: (value: number | null, candidate) => (
                        <Space direction="vertical" size={0}>
                          <Typography.Text>{confidenceLabel(value)}</Typography.Text>
                          <Typography.Text type="secondary">
                            {candidate.confidence_basis} · 未校准
                          </Typography.Text>
                        </Space>
                      ),
                    },
                  ]}
                />
              )}
            </div>

            <Alert
              type="info"
              showIcon
              message="机器线索仍需复核"
              description="页面类型、OCR 需求、分类和字段置信值都是未校准线索，不代表法规结论或报告已生成。"
            />
          </Space>
        )}
      </section>
    </>
  );
}

// —— 文档详情抽屉 ——

function DocumentDrawer({
  documentId,
  clientId,
  capabilities,
  onChanged,
  onRequestUpload,
  onClose,
}: {
  documentId: string;
  clientId?: string;
  capabilities: IngestionCapabilities | null;
  onChanged: () => void;
  onRequestUpload?: () => void;
  onClose: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [detail, setDetail] = useState<DocumentSummary & { versions?: VersionSummary[] }>();
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [versionUploadOpen, setVersionUploadOpen] = useState(false);

  useEffect(() => {
    let active = true;
    setDetail(undefined);
    setError(null);
    getIngestionDocument(getAccessToken(), documentId)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => {
        if (active) setError(userFacingIngestionError(e));
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, documentId, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const latest = detail?.latest_version ?? null;
  const pdfUploadAvailable =
    capabilities?.upload_enabled === true &&
    capabilities.allowed_types.some((item) => item.content_type === "application/pdf");
  const canUploadVersion =
    detail?.allowed_actions.includes("upload_version") === true && pdfUploadAvailable;

  return (
    <Drawer
      title={detail?.display_name ?? "材料详情"}
      width="min(820px, 100vw)"
      open
      onClose={onClose}
      extra={
        canUploadVersion ? (
          <Button size="small" onClick={() => setVersionUploadOpen(true)}>
            上传 PDF 新版本
          </Button>
        ) : null
      }
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message="材料详情加载失败"
          description={error}
          action={<Button size="small" onClick={reload}>重试</Button>}
        />
      ) : !detail ? (
        <Spin />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <section>
            <Typography.Text strong>当前状态</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <StatusDot tone={STAGE_TONE[stageOf(detail)]} label={stageOf(detail)} />
              {latest?.reason_code && (
                <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 13 }}>
                  {reasonCopy(latest.reason_code)}
                </Typography.Text>
              )}
            </div>
            {latest && (
              <div style={{ marginTop: 8 }}>
                <VersionActions version={latest} onDone={reload} />
              </div>
            )}
          </section>
          {latest && (
            <MaterialAnalysisSection
              version={latest}
              clientAccountId={clientId}
              onDone={reload}
              onRequestUpload={onRequestUpload}
            />
          )}
          {latest && (
            <section>
              <Typography.Text strong>安全预览</Typography.Text>
              <div style={{ marginTop: 8 }}>
                <VersionPreview versionId={latest.id} />
              </div>
            </section>
          )}
          <section>
            <Typography.Text strong>版本历史</Typography.Text>
            <div style={{ marginTop: 8 }}>
              {(detail.versions ?? []).map((v) => (
                <div
                  key={v.id}
                  style={{
                    padding: "8px 0",
                    borderTop: "1px solid var(--eco-border)",
                  }}
                >
                  <Typography.Text>
                    第 {v.version_number} 版 · {businessStageOf(v)}
                  </Typography.Text>
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {formatDateTime(v.created_at)}
                      {v.reason_code ? ` · ${reasonCopy(v.reason_code)}` : ""}
                    </Typography.Text>
                  </div>
                  {v.id !== latest?.id && v.allowed_actions.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      <VersionActions version={v} onDone={reload} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
      {detail && (
        <DocumentUploadModal
          open={versionUploadOpen}
          mode="version"
          token={getAccessToken()}
          documentId={detail.id}
          capabilities={capabilities}
          acceptedContentTypes={["application/pdf"]}
          onCancel={() => setVersionUploadOpen(false)}
          onSuccess={() => {
            setVersionUploadOpen(false);
            reload();
            onChanged();
          }}
        />
      )}
    </Drawer>
  );
}

// —— 面板主体 ——

export default function MaterialPanel({
  scope,
  clientId,
}: {
  scope: "shared" | "client";
  clientId?: string;
}) {
  const api = useApi();
  const { getAccessToken } = useAuth();
  const narrow = useNarrow();
  const [rows, setRows] = useState<MaterialItem[] | null>(null);
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [form] = Form.useForm<{ name: string }>();
  const [file, setFile] = useState<File | null>(null);
  const [openDocId, setOpenDocId] = useState<string | null>(null);
  // 上传幂等键：一次未知结果期间保持不变；成功/冲突/重开流程才更新
  const uploadKeyRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    // 域/客户变化：先清旧状态再加载。
    setRows(null);
    setDocs(null);
    setCapabilities(null);
    setCapabilityError(null);
    setError(null);
    if (isMockData) {
      const load =
        scope === "shared" ? api.listSharedMaterials() : api.listClientMaterials(clientId!);
      load
        .then((items) => {
          if (active) setRows(items);
        })
        .catch((e) => {
          if (active) setError(e);
        });
    } else {
      getIngestionCapabilities(getAccessToken())
        .then((caps) => {
          if (active) {
            setCapabilities(caps);
            setCapabilityError(null);
          }
        })
        .catch((requestError: unknown) => {
          // capabilities 不可得 → 不提供可执行按钮（fail-closed）
          if (active) {
            setCapabilities(null);
            setCapabilityError(userFacingIngestionError(requestError));
          }
        });
      listIngestionDocuments(getAccessToken(), {
        scopeKind: scope === "shared" ? "service_provider" : "client",
        clientAccountId: scope === "client" ? clientId : undefined,
        limit: 100,
      })
        .then((collection) => {
          assertScopePure(collection, scope, clientId);
          if (active) setDocs(collection.items);
        })
        .catch((e) => {
          if (active) setError(toApiError(e));
        });
    }
    return () => {
      active = false;
    };
  }, [api, getAccessToken, scope, clientId, nonce]);

  // 活动状态静默刷新：不清空现有列表，不显示加载指示
  useEffect(() => {
    if (isMockData || !docs) return;
    if (!docs.some((d) => ACTIVE_STAGES.has(stageOf(d)))) return;
    const timer = setTimeout(() => {
      listIngestionDocuments(getAccessToken(), {
        scopeKind: scope === "shared" ? "service_provider" : "client",
        clientAccountId: scope === "client" ? clientId : undefined,
        limit: 100,
      })
        .then((collection) => {
          assertScopePure(collection, scope, clientId);
          setDocs(collection.items);
        })
        .catch(() => {
          // 静默刷新失败不打断当前视图，下一轮继续
        });
    }, 5000);
    return () => clearTimeout(timer);
  }, [docs, getAccessToken, scope, clientId]);

  const openUpload = () => {
    uploadKeyRef.current = crypto.randomUUID();
    setUploadOpen(true);
  };

  const closeUpload = () => {
    if (uploading) {
      message.info("文件上传中，完成后才能关闭此窗口");
      return;
    }
    setUploadOpen(false);
    setFile(null);
    form.resetFields();
  };

  const openUploadFromDetail = () => {
    setOpenDocId(null);
    openUpload();
  };

  const upload = useCallback(async () => {
    if (!file) return;
    const validationError = validatePdfFile(file, isMockData ? null : capabilities);
    if (validationError) {
      message.error(validationError);
      return;
    }
    const values = await form.validateFields();
    setUploading(true);
    try {
      if (isMockData) {
        await api.uploadMaterial({
          file,
          name: values.name || file.name,
          scope,
          clientId,
        });
      } else {
        await createIngestionDocument(
          getAccessToken(),
          values.name || file.name,
          file,
          uploadKeyRef.current ?? crypto.randomUUID(),
          undefined,
          "unknown",
          {
            kind: scope === "shared" ? "service_provider" : "client",
            client_account_id: scope === "client" ? (clientId ?? null) : null,
          },
        );
      }
      // 只陈述事实，不伪造进度百分比
      message.success("文件已接收，正在处理");
      uploadKeyRef.current = null; // 明确成功：允许下次生成新键
      setUploadOpen(false);
      setFile(null);
      form.resetFields();
      setNonce((n) => n + 1);
    } catch (e) {
      const code = (e as { code?: string })?.code;
      if (code === "IDEMPOTENCY_CONFLICT") {
        // 明确冲突：更换键并允许重试
        uploadKeyRef.current = crypto.randomUUID();
        message.error("请求标识冲突，请重试");
      } else {
        // 未知结果：保持同一幂等键
        message.error(isMockData ? "上传失败，请重试" : userFacingIngestionError(e));
      }
    } finally {
      setUploading(false);
    }
  }, [api, capabilities, file, form, scope, clientId, getAccessToken]);

  if (error) {
    return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  }

  // 无权限或扫描不可用：不显示可执行按钮
  const uploadAllowed =
    isMockData ||
    (capabilities !== null &&
      capabilities.upload_enabled &&
      capabilities.scanner.state === "ready" &&
      pdfCapabilityOf(capabilities) !== null);
  const uploadBlockReason = !isMockData && capabilities
    ? !capabilities.upload_enabled
      ? reasonCopy(capabilities.disabled_reason_code ?? "SCAN_ENGINE_UNAVAILABLE")
      : capabilities.scanner.state !== "ready"
        ? "安全检查暂不可用，上传已暂停"
        : !pdfCapabilityOf(capabilities)
          ? "当前环境未开放 PDF 上传"
        : null
    : capabilityError
      ? "上传能力读取失败"
      : null;

  const pdfCapability = pdfCapabilityOf(capabilities);
  const pdfMaxBytes = pdfCapability && capabilities
    ? Math.min(pdfCapability.max_file_bytes, capabilities.limits.max_file_bytes)
    : null;
  const pdfAccept = pdfCapability
    ? [pdfCapability.content_type, ...pdfCapability.extensions].join(",")
    : "application/pdf,.pdf";

  const uploadButton = uploadAllowed ? (
    <Button type="primary" onClick={openUpload}>
      {scope === "shared" ? "上传共享 PDF" : "上传客户 PDF"}
    </Button>
  ) : null;

  function uploadModal() {
    return (
      <Modal
        title={scope === "shared" ? "上传共享 PDF" : "上传客户 PDF"}
        open={uploadOpen}
        onOk={() => void upload()}
        onCancel={closeUpload}
        confirmLoading={uploading}
        closable={!uploading}
        maskClosable={!uploading}
        keyboard={!uploading}
        cancelButtonProps={{ disabled: uploading }}
        okButtonProps={{ disabled: !file || uploading }}
        okText="上传"
        cancelText="取消"
        destroyOnHidden
      >
        {uploading && (
          <Alert
            type="info"
            showIcon
            message="文件正在上传"
            description="为避免重复提交，请等待本次请求完成。"
            style={{ marginBottom: 16 }}
          />
        )}
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="材料名称（留空则使用文件名）"
            rules={[{ max: 160, message: "材料名称不能超过 160 个字符" }]}
          >
            <Input disabled={uploading} placeholder="例如：排污许可证" />
          </Form.Item>
          <Form.Item label="文件" required>
            <Upload
              accept={pdfAccept}
              disabled={uploading}
              beforeUpload={(f) => {
                const validationError = validatePdfFile(f, isMockData ? null : capabilities);
                if (validationError) {
                  setFile(null);
                  message.error(validationError);
                  return Upload.LIST_IGNORE;
                }
                setFile(f);
                return false;
              }}
              maxCount={1}
              onRemove={() => {
                if (uploading) return false;
                setFile(null);
                return true;
              }}
            >
              <Button disabled={uploading}>选择 PDF</Button>
            </Upload>
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
              {pdfMaxBytes === null
                ? isMockData ? "仅支持 PDF" : "正在读取 PDF 大小上限"
                : `仅支持 PDF，单个文件不超过 ${formatBytes(pdfMaxBytes)}`}
            </Typography.Text>
          </Form.Item>
        </Form>
      </Modal>
    );
  }

  // —— 演示环境：简表走查 ——
  if (isMockData) {
    return (
      <div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          {uploadButton}
        </div>
        <Table<MaterialItem>
          rowKey="id"
          loading={rows === null}
          dataSource={rows ?? []}
          pagination={false}
          locale={{ emptyText: scope === "shared" ? "暂无共享材料" : "暂无指定客户材料" }}
          columns={[
            { title: "名称", dataIndex: "name" },
            {
              title: "状态",
              dataIndex: "status",
              width: 120,
              render: (status: MaterialStatus) => (
                <StatusDot
                  tone={
                    { processing: "processing", ready: "success", blocked: "warning", failed: "danger" }[
                      status
                    ] as StatusTone
                  }
                  label={MATERIAL_STATUS_LABEL[status]}
                />
              ),
            },
            { title: "版本数", dataIndex: "versionCount", width: 90 },
            {
              title: "更新时间",
              dataIndex: "updatedAt",
              width: 160,
              render: (iso: string) => (
                <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
              ),
            },
          ]}
        />
        {uploadModal()}
      </div>
    );
  }

  // —— 正式 HTTP ——
  const counts = { total: docs?.length ?? 0, processing: 0, pendingConfirm: 0, failed: 0, done: 0 };
  for (const d of docs ?? []) {
    const stage = stageOf(d);
    if (ACTIVE_STAGES.has(stage)) counts.processing += 1;
    else if (stage === "待确认") counts.pendingConfirm += 1;
    else if (stage === "处理失败") counts.failed += 1;
    else if (stage === "入库处理完成") counts.done += 1;
  }

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        {docs === null
          ? "正在加载材料…"
          : `共 ${counts.total} 份 · 处理中 ${counts.processing} · 待确认 ${counts.pendingConfirm} · 失败 ${counts.failed} · 入库处理完成 ${counts.done}`}
        {uploadBlockReason ? `（${uploadBlockReason}）` : ""}
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary" style={{ marginTop: -6, marginBottom: 12, fontSize: 13 }}>
        列表展示安全入库状态；知识索引和报告进度请打开“详情”，详情处理中会自动刷新。
      </Typography.Paragraph>
      {capabilityError && (
        <Alert
          type="warning"
          showIcon
          message="上传能力暂不可用"
          description={capabilityError}
          action={<Button size="small" onClick={() => setNonce((n) => n + 1)}>重试</Button>}
          style={{ marginBottom: 12 }}
        />
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        {uploadButton}
      </div>
      {narrow ? (
        docs === null ? (
          <Spin style={{ display: "block", margin: "48px auto" }} />
        ) : docs.length === 0 ? (
          <Typography.Text type="secondary">
            {scope === "shared" ? "暂无共享材料" : "暂无指定客户材料"}
          </Typography.Text>
        ) : (
          <div>
            {docs.map((d) => (
              <div key={d.id} className="client-mobile-item">
                <Button
                  type="link"
                  style={{ padding: 0, fontSize: 15 }}
                  onClick={() => setOpenDocId(d.id)}
                >
                  {d.display_name}
                </Button>
                <div className="client-mobile-meta">
                  <StatusDot tone={STAGE_TONE[stageOf(d)]} label={stageOf(d)} />
                  <span style={{ marginLeft: 8 }}>
                    {MATERIAL_KIND_LABEL[d.declared_material_kind]} · 第 {d.version_count} 版 ·{" "}
                    {formatDateTime(d.created_at)}
                  </span>
                </div>
                {d.latest_version?.reason_code && (
                  <div className="client-mobile-meta">
                    {reasonCopy(d.latest_version.reason_code)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      ) : (
        <Table<DocumentSummary>
          rowKey="id"
          loading={docs === null}
          dataSource={docs ?? []}
          pagination={false}
          locale={{ emptyText: scope === "shared" ? "暂无共享材料" : "暂无指定客户材料" }}
          columns={[
            {
              title: "文件名",
              dataIndex: "display_name",
              render: (name: string, row) => (
                <Button type="link" style={{ padding: 0 }} onClick={() => setOpenDocId(row.id)}>
                  {name}
                </Button>
              ),
            },
            {
              title: "分类",
              dataIndex: "declared_material_kind",
              width: 100,
              render: (kind: MaterialKind) => MATERIAL_KIND_LABEL[kind] ?? "未分类",
            },
            { title: "版本", dataIndex: "version_count", width: 70 },
            {
              title: "上传时间",
              dataIndex: "created_at",
              width: 150,
              render: (iso: string) => (
                <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
              ),
            },
            {
              title: "业务状态",
              key: "stage",
              render: (_, row) => (
                <span>
                  <StatusDot tone={STAGE_TONE[stageOf(row)]} label={stageOf(row)} />
                  {row.latest_version?.reason_code && (
                    <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      {reasonCopy(row.latest_version.reason_code)}
                    </Typography.Text>
                  )}
                </span>
              ),
            },
            {
              title: "操作",
              key: "actions",
              width: 90,
              render: (_, row) => (
                <Button type="link" style={{ padding: 0 }} onClick={() => setOpenDocId(row.id)}>
                  详情
                </Button>
              ),
            },
          ]}
        />
      )}
      {openDocId && (
        <DocumentDrawer
          documentId={openDocId}
          clientId={scope === "client" ? clientId : undefined}
          capabilities={capabilities}
          onChanged={() => setNonce((n) => n + 1)}
          onRequestUpload={uploadAllowed ? openUploadFromDetail : undefined}
          onClose={() => setOpenDocId(null)}
        />
      )}
      {uploadModal()}
    </div>
  );
}
