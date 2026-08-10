const REASON_COPY: Record<string, string> = {
  FILE_TOO_LARGE: "文件超过当前资源上限",
  FILE_TYPE_NOT_ALLOWED: "当前文件格式不受支持",
  CONTAINER_MISMATCH: "文件内容与声明的格式不一致",
  EMPTY_FILE: "不能上传空文件",
  DOCUMENT_VERSION_LIMIT: "该文档的版本数量已达到上限",
  SCAN_ENGINE_UNAVAILABLE: "本地安全扫描服务当前不可用",
  MALWARE_SCAN_NOT_CONFIGURED: "本地安全扫描服务尚未配置",
  MALWARE_DETECTED: "本地安全扫描发现风险，文件仍被隔离",
  PREVIEW_RESOURCE_LIMIT: "文件超出安全预览资源限制",
  PREVIEW_FAILED: "安全预览生成失败",
  SOURCE_OBJECT_MISSING: "隔离区中的源文件不可用",
  SOURCE_OBJECT_STAT_FAILED: "暂时无法读取隔离区文件",
  SOURCE_OBJECT_READ_FAILED: "暂时无法读取隔离区文件",
  ILLEGAL_STATE_TRANSITION: "当前状态不允许执行该操作",
  IDEMPOTENCY_CONFLICT: "请求标识与先前请求不一致",
  TENANT_CONTEXT_REQUIRED: "请先选择企业",
  NOT_FOUND: "记录不存在或当前企业无权访问",
  REQUEST_ABORTED: "请求已取消",
  NETWORK_ERROR: "网络请求失败，请稍后重试",
};

const STATUS_COPY: Record<string, string> = {
  received: "已接收",
  processing: "处理中",
  ready: "可用",
  blocked: "已阻断",
  failed: "失败",
  held: "隔离中",
  released: "已解除隔离",
  queued: "等待处理",
  scanning: "扫描中",
  clean: "扫描通过",
  infected: "发现风险",
  error: "处理异常",
  unavailable: "不可用",
  generating: "生成预览",
};

const MIME_COPY: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "image/jpeg": "JPEG",
};

export function reasonCopy(code: string | null | undefined): string {
  if (!code) return "处理未完成";
  const normalized = /^[A-Z0-9_]{1,80}$/.test(code) ? code : "UNKNOWN_REASON";
  return REASON_COPY[normalized] ?? `处理未完成（${normalized}）`;
}
export function statusCopy(status: string): string {
  return STATUS_COPY[status] ?? status;
}

export function statusColor(status: string): string {
  if (["ready", "released", "clean"].includes(status)) return "green";
  if (["infected", "failed", "blocked", "error"].includes(status)) return "red";
  if (["processing", "scanning", "generating"].includes(status)) return "blue";
  if (["held", "queued", "received"].includes(status)) return "gold";
  if (status === "unavailable") return "orange";
  return "default";
}

export function mimeCopy(contentType: string): string {
  return MIME_COPY[contentType] ?? contentType;
}

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KiB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MiB`;
  return `${(size / 1024 ** 3).toFixed(1)} GiB`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

export function spreadsheetColumnLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}
