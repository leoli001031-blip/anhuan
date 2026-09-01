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
  MATERIAL_PDF_PARSE_FAILED: "PDF 结构无法形成可靠的录入草稿",
  MATERIAL_PDF_ENCRYPTED: "加密 PDF 暂不能生成录入草稿",
  MATERIAL_PDF_PAGE_LIMIT: "PDF 页数超过材料分析上限",
  MATERIAL_SOURCE_IDENTITY_MISMATCH: "材料原件身份校验不一致",
  MATERIAL_SOURCE_READ_FAILED: "暂时无法读取材料原件",
  MATERIAL_ANALYSIS_FAILED: "材料机器分析未完成",
  MATERIAL_ANALYSIS_RETRY_REQUIRED: "材料自动分析暂未完成，可重新处理",
  MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED:
    "该分析已人工确认，但仍有页面需要 OCR，请由管理员复核后再处理",
  MATERIAL_INTAKE_UNAVAILABLE: "材料分析服务暂时不可用",
  AUTO_PIPELINE_PDF_ONLY: "自动处理流水线仅适用于 PDF",
  INGESTION_PROCESSING: "正在执行安全入库",
  INGESTION_RETRY_WAIT: "安全入库等待重试",
  INGESTION_UNAVAILABLE: "安全入库暂不可用",
  MATERIAL_INGESTION_DISABLED: "当前环境未启用持久化安全入库",
  MATERIAL_INGESTION_ACTOR_REVOKED: "启动安全入库的管理员权限已失效",
  MATERIAL_INGESTION_ACTOR_REBIND_REQUIRED: "历史安全入库任务需要当前管理员重新接管",
  MATERIAL_INGESTION_DELIVERY_FAILED: "安全入库任务暂时中断",
  MATERIAL_INGESTION_QUEUE_UNAVAILABLE: "安全入库队列暂时不可用",
  MATERIAL_INGESTION_RETRIES_EXHAUSTED: "安全入库已达重试上限",
  MATERIAL_ANALYSIS_PENDING: "PDF 分析等待处理",
  OCR_REQUIRED: "仍有页面需要 OCR",
  OCR_DISABLED: "当前环境未启用 OCR",
  OCR_UNAVAILABLE: "OCR 服务当前不可用",
  OCR_PAGE_LIMIT: "需要 OCR 的页数超过本地处理上限",
  OCR_OUTPUT_INSUFFICIENT: "OCR 未识别出足够文字",
  MATERIAL_INDEX_RUNNING: "正在写入知识索引",
  MATERIAL_INDEX_PENDING: "知识索引等待处理",
  MATERIAL_INDEX_FAILED: "知识索引未完成",
  MATERIAL_INDEX_STATUS_UNAVAILABLE: "暂时无法确认知识索引状态",
  LOCAL_INDEX_DISABLED: "当前环境未启用本地索引",
  MATERIAL_PIPELINE_DISABLED: "当前环境未启用自动处理流水线",
  MATERIAL_PIPELINE_ACTOR_REBIND_REQUIRED:
    "历史自动处理任务需要当前企业管理员重新处理并接管",
  MATERIAL_PIPELINE_DELIVERY_BLOCKED: "自动处理任务已阻断",
  MATERIAL_PIPELINE_DELIVERY_FAILED: "自动处理任务暂时中断",
  MATERIAL_PIPELINE_QUEUE_UNAVAILABLE: "自动处理队列暂时不可用",
  MATERIAL_PIPELINE_DISPATCH_FAILED: "自动处理任务投递失败",
  MATERIAL_PIPELINE_RETRIES_EXHAUSTED: "自动处理已达重试上限",
  REPORT_WAITING_FOR_INDEX: "分析报告正在等待知识索引",
  REPORT_GENERATING: "正在生成分析报告",
  REPORT_GENERATION_QUEUED: "分析报告已进入生成队列",
  REPORT_GENERATION_PENDING: "分析报告等待生成",
  REPORT_GENERATION_FAILED: "分析报告生成失败",
  REPORT_GENERATION_DISABLED: "当前环境未启用报告生成",
  REPORT_QUEUE_DISPATCH_FAILED: "报告生成任务投递失败，可恢复原任务",
  REPORT_QUEUE_STATUS_UNAVAILABLE: "暂时无法确认报告队列状态",
  REPORT_GENERATION_RETRIES_EXHAUSTED: "报告生成重试已达上限，可恢复原任务",
  REPORT_WORKER_GENERATION_DISABLED: "报告生成工作进程未启用，可恢复原任务",
  REPORT_ACTOR_REVOKED: "启动报告的管理员权限已失效",
  REPORT_SOURCE_FINGERPRINT_CHANGED: "报告来源材料已变更，请重新生成",
  REPORT_SOURCE_EVIDENCE_INVALID: "报告来源证据校验失败",
  REPORT_SOURCE_EVIDENCE_HASH_MISMATCH: "报告来源证据完整性校验失败",
  REPORT_REVIEW_REQUIRED: "现有报告正在等待复核",
  REPORT_CLIENT_BINDING_REQUIRED: "当前客户尚未建立报告关联",
  REPORT_CLIENT_SCOPE_REQUIRED: "分析报告仅对客户资料生成",
  REPORT_CLIENT_SOURCES_EMPTY: "当前客户资料不足",
  REPORT_PROVIDER_REQUIRED: "当前身份不能启动报告生成",
  REPORT_PROVIDER_SOURCES_MISSING: "环保服务公司资料不足",
  REPORT_SOURCES_INCOMPLETE: "生成报告所需的资料不完整",
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
