// SessionAccess：唯一身份面（合同 GET /api/v1/session/access）。
// 客户身份只从 Bearer 会话推导；前端永不从 URL、localStorage 或用户输入指定企业/客户身份。
// localStorage 的企业选择只是 membership 提示，响应 enterprise_id 必须与请求头一致。
// 前端角色门仅控制体验，不构成安全边界——所有权限以后端为准。
import type { ProductRole, SessionAccessV1 } from "./types";

export interface SessionAccess {
  getSessionAccess(): Promise<SessionAccessV1>;
}

export function homePathFor(role: ProductRole): string {
  return role === "provider_admin" ? "/console/clients" : "/portal/qa";
}

export function canAccessConsole(session: SessionAccessV1): boolean {
  return session.product_role === "provider_admin";
}

export function canAccessPortal(session: SessionAccessV1): boolean {
  return session.product_role === "client_user";
}

export function canAccessLegacyProvider(session: SessionAccessV1): boolean {
  return session.product_role === "provider_admin";
}
