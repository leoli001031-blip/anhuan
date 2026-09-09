// SessionAccess：唯一身份面（合同 GET /api/v1/session/access）。
// 客户身份只从 Bearer 会话推导；前端永不从 URL、localStorage 或用户输入指定企业/客户身份。
// localStorage 的企业选择只是 membership 提示，响应 enterprise_id 必须与请求头一致。
// 前端角色门仅控制体验，不构成安全边界——所有权限以后端为准。
import type { ProductRole, SessionAccessV2 } from "./types";

export interface SessionAccess {
  getSessionAccess(): Promise<SessionAccessV2>;
}

export function homePathFor(role: ProductRole): string {
  // 客户正式首页是 /portal（由门户路由决定默认落点），不硬跳问答。
  if (role === "provider_admin") return "/console/clients";
  if (role === "client_user") return "/portal";
  if (role === "technical_admin") return "/admin";
  if (role === "provider_consultant" || role === "provider_reviewer") return "/workbench";
  return "/account-setup";
}

export function canAccessConsole(session: SessionAccessV2): boolean {
  return session.product_role === "provider_admin";
}

export function canAccessPortal(session: SessionAccessV2): boolean {
  return session.product_role === "client_user";
}

export function canManageMembers(session: SessionAccessV2): boolean {
  return session.membership_role === "enterprise_admin" &&
    (session.product_role === "provider_admin" || session.product_role === "client_user");
}

export function canAccessLegacyProvider(session: SessionAccessV2): boolean {
  return ["provider_admin", "provider_consultant", "provider_reviewer", "technical_admin"].includes(session.product_role);
}


export function canAccessLegacyPath(session: SessionAccessV2, pathname: string): boolean {
  if (session.product_role === "provider_admin") return true;
  const root = "/" + pathname.split("/").filter(Boolean)[0];
  if (session.product_role === "technical_admin") {
    return ["/admin", "/audit", "/invite", "/enterprises"].includes(root);
  }
  if (session.product_role === "provider_consultant") {
    return ["/workbench", "/calendar", "/notifications", "/service-cases", "/my-tasks", "/findings", "/rectification"].includes(root);
  }
  if (session.product_role === "provider_reviewer") {
    return ["/workbench", "/calendar", "/notifications", "/service-cases", "/my-tasks", "/findings", "/reviews"].includes(root);
  }
  return false;
}
