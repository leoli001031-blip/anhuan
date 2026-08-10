const REASON_COPY: Record<string, string> = {
  TENANT_CONTEXT_REQUIRED: "请先选择企业",
  NOT_FOUND: "记录不存在或当前企业无权访问",
  QUALITY_SUITE_NOT_FOUND: "质量套件不存在或当前企业无权访问",
  QUALITY_RUN_NOT_FOUND: "质量运行不存在或当前企业无权访问",
  QUALITY_DISAGREEMENT_NOT_FOUND: "分歧记录不存在或当前企业无权访问",
  QUALITY_SUITE_ARCHIVED: "已归档套件不能新增场景或运行",
  QUALITY_NO_ENABLED_SCENARIOS: "当前套件没有启用的合成场景",
  QUALITY_JSON_INVALID: "Oracle结构超出有限JSON合同",
  QUALITY_REVIEW_TERMINAL: "该分歧已经完成处置",
  QUALITY_REVIEW_CONFLICT: "分歧已由其他用户处置，请刷新",
  REQUEST_ABORTED: "请求已取消",
  NETWORK_ERROR: "网络请求失败，请稍后重试",
  INVALID_RESPONSE: "服务返回了无法识别的数据",
  INVALID_P6_PATH: "页面请求地址不受支持",
};

const CATEGORY_COPY: Record<string, string> = {
  ingestion: "受控导入",
  retrieval: "检索",
  qa: "问答",
  authorization: "权限隔离",
  injection: "注入防护",
};

const SCENARIO_COPY: Record<string, string> = {
  exact_match: "精确匹配",
  threshold: "阈值",
  refusal_required: "必须拒答",
  isolation_required: "必须隔离",
  injection_blocked: "必须阻断注入",
  disagreement_max: "分歧上限",
};

const RUN_STATUS_COPY: Record<string, string> = {
  queued: "等待运行",
  running: "运行中",
  passed: "合成检查通过",
  failed: "合成检查失败",
  cancelled: "已取消",
};

const RESULT_STATUS_COPY: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  error: "执行错误",
};

const KIND_COPY: Record<string, string> = {
  parser: "Parser分歧",
  ocr: "OCR分歧",
  citation: "引用分歧",
  refusal: "拒答分歧",
  authorization: "权限分歧",
  injection: "注入分歧",
};

const REVIEW_COPY: Record<string, string> = {
  open: "待处置",
  acknowledged: "已确认",
  waived: "已豁免",
};

const METRIC_COPY: Record<string, string> = {
  active_suites: "活跃套件",
  enabled_scenarios: "启用场景",
  total_runs: "合成运行",
  passed_runs: "通过运行",
  failed_runs: "失败运行",
  passed_results: "通过结果",
  failed_results: "失败结果",
  error_results: "错误结果",
  open_disagreements: "未处置分歧",
};

export function p6ReasonCopy(code: string | null | undefined): string {
  if (!code) return "操作未完成";
  const normalized = /^[A-Z0-9_]{1,96}$/.test(code) ? code : "UNKNOWN_REASON";
  return REASON_COPY[normalized] ?? `操作未完成（${normalized}）`;
}

export function qualityCategoryCopy(value: string): string {
  return CATEGORY_COPY[value] ?? value;
}

export function scenarioTypeCopy(value: string): string {
  return SCENARIO_COPY[value] ?? value;
}

export function qualityRunStatusCopy(value: string): string {
  return RUN_STATUS_COPY[value] ?? value;
}

export function qualityResultStatusCopy(value: string): string {
  return RESULT_STATUS_COPY[value] ?? value;
}

export function disagreementKindCopy(value: string): string {
  return KIND_COPY[value] ?? value;
}

export function disagreementReviewCopy(value: string): string {
  return REVIEW_COPY[value] ?? value;
}

export function qualityMetricCopy(value: string): string {
  return METRIC_COPY[value] ?? value.replaceAll("_", " ");
}

export function formatP6DateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

export function runStatusColor(value: string): string {
  if (value === "passed") return "green";
  if (value === "failed") return "red";
  if (value === "running") return "blue";
  if (value === "queued") return "gold";
  return "default";
}

export function resultStatusColor(value: string): string {
  if (value === "passed") return "green";
  if (value === "failed" || value === "error") return "red";
  return "default";
}

export function severityColor(value: string): string {
  if (value === "critical") return "red";
  if (value === "high") return "orange";
  if (value === "medium") return "blue";
  return "default";
}

export function disagreementReviewColor(value: string): string {
  if (value === "acknowledged") return "blue";
  if (value === "waived") return "default";
  return "gold";
}
