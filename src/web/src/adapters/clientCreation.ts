import { ApiError } from "./errors";
import type { CreateCrmAccountInput } from "../features/p4/types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STAGES = ["lead", "active", "dormant", "closed"];

export function assertClientRequestId(value: unknown): asserts value is string {
  if (typeof value !== "string" || !UUID.test(value)) throw new ApiError(422, "CRM_ACCOUNT_REQUEST_ID_REQUIRED", false);
}

// The signature includes every create field, including explicit defaults. The
// request id belongs to this draft and is reused if the response is uncertain.
export function normalizeClientCreate(input: CreateCrmAccountInput) {
  const display_name = input.display_name.trim();
  if (!display_name || Array.from(display_name).length > 200 || !STAGES.includes(input.stage)) {
    throw new ApiError(422, "CRM_ACCOUNT_INPUT_INVALID", false);
  }
  const next = input.next_follow_up_at ? new Date(input.next_follow_up_at) : null;
  if (next && Number.isNaN(next.getTime())) throw new ApiError(422, "CRM_ACCOUNT_INPUT_INVALID", false);
  return {
    display_name,
    stage: input.stage,
    owner_user_id: input.owner_user_id?.trim().toLowerCase() || null,
    industry_note: input.industry_note?.trim() || null,
    region_note: input.region_note?.trim() || null,
    next_follow_up_at: next?.toISOString() ?? null,
  };
}

export function assertClientCreateReceipt(value: unknown, enterpriseId: string | null, status: number): void {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  if (!raw || !enterpriseId || raw.enterprise_id !== enterpriseId) {
    throw new ApiError(409, "CRM_ACCOUNT_ENTERPRISE_MISMATCH", false);
  }
  if (status !== 201 || typeof raw.id !== "string" || !UUID.test(raw.id)
    || typeof raw.display_name !== "string" || !raw.display_name.trim()
    || typeof raw.stage !== "string" || !STAGES.includes(raw.stage)
    || typeof raw.updated_at !== "string" || Number.isNaN(Date.parse(raw.updated_at))
    || !["industry_note", "region_note"].every(key => raw[key] === null || typeof raw[key] === "string")
    || !(raw.next_follow_up_at === null || (typeof raw.next_follow_up_at === "string" && !Number.isNaN(Date.parse(raw.next_follow_up_at))))) {
    throw new ApiError(409, "CRM_ACCOUNT_RECEIPT_INVALID", false);
  }
  // A replay returns the current client. Its name/stage may have been edited
  // since creation, so those fields must not be compared with the old draft.
}

export function clientCreationError(error: unknown): string {
  const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  if (code === "PENDING_WRITE_STORAGE_UNAVAILABLE") return "浏览器未能保存本次请求信息，请允许当前网站使用会话存储后重试。";
  if (code === "PENDING_WRITE_IDENTITY_REQUIRED") return "登录信息尚未完整，请刷新登录信息后再试。";
  if (code === "CRM_MANAGER_REQUIRED") return "你当前没有创建客户的权限，请刷新登录信息后再试。";
  if (code === "CRM_ACCOUNT_REQUEST_CONFLICT") return "这次创建的内容与先前请求不一致，请核对客户列表后重新填写。";
  if (code === "CRM_ACCOUNT_INPUT_INVALID" || code === "CRM_ACCOUNT_NAME_REQUIRED") return "请填写有效的客户名称和阶段。";
  if (code === "CRM_ACCOUNT_REQUEST_ID_REQUIRED") return "创建请求信息不完整，请刷新页面后重新填写。";
  if (code === "CRM_ACCOUNT_ENTERPRISE_MISMATCH") return "未能确认客户所属企业，请保留当前内容并重试本次创建。";
  return "创建结果尚未确认，请保留当前内容并重试本次创建；重试会核对原请求。";
}
