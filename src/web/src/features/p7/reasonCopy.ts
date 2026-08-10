const ERROR_COPY: Record<string, string> = {
  TENANT_CONTEXT_REQUIRED: "请先选择企业",
  NOT_FOUND: "记录不存在或当前企业无权访问",
  REHEARSAL_PLAN_NOT_FOUND: "演练计划不存在或当前企业无权访问",
  REHEARSAL_RUN_NOT_FOUND: "演练运行不存在或当前企业无权访问",
  REHEARSAL_RESULT_NOT_FOUND: "演练检查结果不存在或当前企业无权访问",
  REHEARSAL_NO_ENABLED_CHECKS: "计划没有启用的检查项",
  REHEARSAL_RUN_NOT_RUNNING: "当前演练不允许继续记录",
  REHEARSAL_RESULT_IMMUTABLE: "该检查结果已冻结，不能重复修改",
  REHEARSAL_REQUIRED_CHECKS_PENDING: "必需检查尚未全部记录",
  REHEARSAL_RUN_STATE_CONFLICT: "演练状态已变化，请刷新",
  REQUEST_ABORTED: "请求已取消",
  NETWORK_ERROR: "网络请求失败，请稍后重试",
  INVALID_RESPONSE: "服务返回了无法识别的数据",
  INVALID_P7_PATH: "页面请求地址不受支持",
  MANUAL_CHECK_PASSED: "人工计划检查通过",
  MANUAL_CHECK_FAILED: "人工计划检查失败",
  MANUAL_CHECK_BLOCKED: "人工计划检查阻断",
};

const PLAN_STATUS_COPY: Record<string, string> = { draft: "草稿", active: "可演练", archived: "已归档" };
const RUN_STATUS_COPY: Record<string, string> = { planned: "已计划", running: "进行中", passed: "已通过", failed: "未通过", cancelled: "已取消" };
const RESULT_STATUS_COPY: Record<string, string> = { pending: "待记录", passed: "通过", failed: "失败", blocked: "阻断" };
const CATEGORY_COPY: Record<string, string> = {
  service: "服务",
  dependency: "依赖",
  backup: "备份",
  restore: "恢复",
  security: "安全",
  rollback: "回滚",
};

export function p7ReasonCopy(code: string | null | undefined): string {
  if (!code) return "未提供原因码";
  const normalized = /^[A-Z0-9_]{1,96}$/.test(code) ? code : "UNKNOWN_REASON";
  return ERROR_COPY[normalized] ?? `未识别原因（${normalized}）`;
}

export function rehearsalPlanStatusCopy(value: string): string { return PLAN_STATUS_COPY[value] ?? value; }
export function rehearsalRunStatusCopy(value: string): string { return RUN_STATUS_COPY[value] ?? value; }
export function rehearsalResultStatusCopy(value: string): string { return RESULT_STATUS_COPY[value] ?? value; }
export function rehearsalCategoryCopy(value: string): string { return CATEGORY_COPY[value] ?? value; }

export function formatP7DateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

export function rehearsalRunColor(value: string): string {
  if (value === "passed") return "green";
  if (value === "failed") return "red";
  if (value === "running") return "blue";
  if (value === "planned") return "gold";
  return "default";
}

export function rehearsalResultColor(value: string): string {
  if (value === "passed") return "green";
  if (value === "failed" || value === "blocked") return "red";
  if (value === "pending") return "gold";
  return "default";
}
