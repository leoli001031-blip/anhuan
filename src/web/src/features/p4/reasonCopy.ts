const REASON_COPY: Record<string, string> = {
  TENANT_CONTEXT_REQUIRED: "请先选择企业",
  NOT_FOUND: "记录不存在或当前企业无权访问",
  ILLEGAL_STATE_TRANSITION: "当前状态不允许执行该操作",
  INVALID_RESPONSE: "服务返回了无法识别的数据",
  INVALID_P4_PATH: "页面请求地址不受支持",
  REQUEST_ABORTED: "请求已取消",
  NETWORK_ERROR: "网络请求失败，请稍后重试",
  CRM_FIXTURE_ONLY: "CRM当前只允许内部合成数据",
  REPORT_ARCHIVED: "已归档报告不能继续生成版本",
};

const VIEW_COPY: Record<string, string> = {
  admin: "平台管理驾驶舱",
  consultant: "顾问驾驶舱",
  partner: "合作伙伴驾驶舱",
  enterprise: "企业驾驶舱",
};

const METRIC_COPY: Record<string, string> = {
  active_service_cases: "进行中服务",
  active_services: "进行中服务",
  upcoming_site_visits: "未来现场服务",
  upcoming_visits: "未来现场服务",
  open_findings: "开放问题",
  overdue_findings: "逾期问题",
  pending_reviews: "待复核",
  controlled_documents_ready: "可用受控文档",
  controlled_documents_blocked: "受阻受控文档",
  report_count: "业务报告",
  reports: "业务报告",
  business_reports: "业务报告",
  crm_follow_ups_due: "CRM待跟进",
  crm_follow_ups: "CRM待跟进",
};

const QUEUE_COPY: Record<string, string> = {
  services: "服务待办",
  service_cases: "服务待办",
  visits: "现场服务",
  site_visits: "现场服务",
  findings: "问题整改",
  reviews: "待复核事项",
  documents: "受控文档",
  reports: "业务报告",
  crm_follow_ups: "客户跟进",
};

const STAGE_COPY: Record<string, string> = {
  lead: "线索",
  active: "活跃",
  dormant: "暂缓",
  closed: "已关闭",
};

const REPORT_STATUS_COPY: Record<string, string> = {
  active: "进行中",
  archived: "已归档",
};

const VERSION_LIFECYCLE_COPY: Record<string, string> = {
  current: "当前版本",
  superseded: "历史版本",
  void: "已作废",
};

const CHANNEL_COPY: Record<string, string> = {
  onsite: "现场",
  meeting: "会议",
  phone: "电话",
  internal_note: "内部记录",
};

export function p4ReasonCopy(code: string | null | undefined): string {
  if (!code) return "操作未完成";
  const normalized = /^[A-Z0-9_]{1,80}$/.test(code) ? code : "UNKNOWN_REASON";
  return REASON_COPY[normalized] ?? `操作未完成（${normalized}）`;
}

export function dashboardViewCopy(view: string): string {
  return VIEW_COPY[view] ?? "角色驾驶舱";
}

export function metricCopy(metric: string): string {
  return METRIC_COPY[metric] ?? metric.replaceAll("_", " ");
}

export function queueCopy(queue: string): string {
  return QUEUE_COPY[queue] ?? queue.replaceAll("_", " ");
}

export function crmStageCopy(stage: string): string {
  return STAGE_COPY[stage] ?? stage;
}

export function reportStatusCopy(status: string): string {
  return REPORT_STATUS_COPY[status] ?? status;
}

export function versionLifecycleCopy(lifecycle: string): string {
  return VERSION_LIFECYCLE_COPY[lifecycle] ?? lifecycle;
}

export function followUpChannelCopy(channel: string): string {
  return CHANNEL_COPY[channel] ?? channel;
}

export function formatP4DateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

export function formatP4Bytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / 1024 ** 2).toFixed(1)} MiB`;
}

export function stageColor(stage: string): string {
  if (stage === "active") return "green";
  if (stage === "lead") return "blue";
  if (stage === "dormant") return "gold";
  return "default";
}

export function lifecycleColor(lifecycle: string): string {
  if (lifecycle === "current") return "green";
  if (lifecycle === "void") return "red";
  return "default";
}
