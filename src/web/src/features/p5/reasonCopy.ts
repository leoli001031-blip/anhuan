const REASON_COPY: Record<string, string> = {
  TENANT_CONTEXT_REQUIRED: "请先选择企业",
  NOT_FOUND: "记录不存在或当前企业无权访问",
  ILLEGAL_STATE_TRANSITION: "当前状态不允许执行该操作",
  POLICY_SOURCE_NOT_FOUND: "政策来源不存在或当前企业无权访问",
  POLICY_VERSION_NOT_FOUND: "候选版本不存在或当前企业无权访问",
  POLICY_IMPACT_NOT_FOUND: "影响候选不存在或当前企业无权访问",
  P5_DOCUMENT_NOT_READY: "关联文档尚未完成受控处理",
  P5_SUBMITTER_CANNOT_APPROVE: "提交人不能审批自己本轮提交的候选",
  P5_VERSION_NOT_APPROVED: "只有已通过或已内部发布的版本可建立影响候选",
  P5_OWNER_NOT_FOUND: "任务负责人不是当前企业成员",
  REQUEST_ABORTED: "请求已取消",
  NETWORK_ERROR: "网络请求失败，请稍后重试",
  INVALID_RESPONSE: "服务返回了无法识别的数据",
  INVALID_P5_PATH: "页面请求地址不受支持",
};

const SOURCE_TYPE_COPY: Record<string, string> = {
  law: "法律",
  regulation: "法规",
  standard: "标准",
  guidance: "指导文件",
  internal: "内部材料",
};

const DOMAIN_COPY: Record<string, string> = {
  safety: "安全",
  health: "职业健康",
  environment: "环境",
  fire: "消防",
  chemical: "化学品",
  general: "综合",
};

const EFFECT_COPY: Record<string, string> = {
  unknown: "未知候选",
  not_effective: "尚未生效候选",
  effective: "有效候选",
  expired: "失效候选",
};

const WORKFLOW_COPY: Record<string, string> = {
  draft: "草稿",
  in_review: "审核中",
  approved: "已通过",
  rejected: "已退回",
  published: "内部发布",
  superseded: "已被替代",
};

const REVIEW_COPY: Record<string, string> = {
  submitted: "提交审核",
  approved: "审核通过",
  rejected: "退回修改",
  published: "内部发布",
};

const PRIORITY_COPY: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "紧急",
};

const IMPACT_STATUS_COPY: Record<string, string> = {
  open: "待研判",
  accepted: "已接受候选",
  dismissed: "已排除候选",
};

const TASK_STATUS_COPY: Record<string, string> = {
  open: "待开始",
  in_progress: "进行中",
  completed: "已完成",
  dismissed: "已关闭",
};

export function p5ReasonCopy(code: string | null | undefined): string {
  if (!code) return "操作未完成";
  const normalized = /^[A-Z0-9_]{1,80}$/.test(code) ? code : "UNKNOWN_REASON";
  return REASON_COPY[normalized] ?? `操作未完成（${normalized}）`;
}

export function sourceTypeCopy(value: string): string {
  return SOURCE_TYPE_COPY[value] ?? value;
}

export function policyDomainCopy(value: string): string {
  return DOMAIN_COPY[value] ?? value;
}

export function effectStatusCopy(value: string): string {
  return EFFECT_COPY[value] ?? value;
}

export function workflowStatusCopy(value: string): string {
  return WORKFLOW_COPY[value] ?? value;
}

export function reviewActionCopy(value: string): string {
  return REVIEW_COPY[value] ?? value;
}

export function impactPriorityCopy(value: string): string {
  return PRIORITY_COPY[value] ?? value;
}

export function impactStatusCopy(value: string): string {
  return IMPACT_STATUS_COPY[value] ?? value;
}

export function impactTaskStatusCopy(value: string): string {
  return TASK_STATUS_COPY[value] ?? value;
}

export function formatP5Date(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("zh-CN");
}

export function formatP5DateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

export function workflowColor(value: string): string {
  if (value === "published") return "green";
  if (value === "approved") return "cyan";
  if (value === "in_review") return "blue";
  if (value === "rejected") return "red";
  if (value === "superseded") return "default";
  return "gold";
}

export function effectColor(value: string): string {
  if (value === "effective") return "green";
  if (value === "expired") return "default";
  if (value === "not_effective") return "blue";
  return "gold";
}

export function priorityColor(value: string): string {
  if (value === "critical") return "red";
  if (value === "high") return "orange";
  if (value === "medium") return "blue";
  return "default";
}
