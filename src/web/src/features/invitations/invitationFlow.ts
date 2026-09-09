// An invitation is a bearer credential, never an authority for tenant/role.
// Keep it only in this tab for the OIDC round trip; the server consumes it.
const STORAGE_KEY = "anhuan.pending-invitation.v1";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const TOKEN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function invitationToken(value: string): string {
  const token = value.trim();
  if (token.length > 8192 || !TOKEN.test(token)) throw new Error("INVITE_INPUT_INVALID");
  return token;
}

export function invitationLink(token: string, origin: string): string {
  return `${origin}/join#invite=${encodeURIComponent(invitationToken(token))}`;
}

export function rememberInvitation(token: string, storage: Storage = sessionStorage): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ token: invitationToken(token), savedAt: Date.now() }));
  } catch {
    throw new Error("INVITE_STORAGE_UNAVAILABLE");
  }
}

export function forgetInvitation(storage: Storage = sessionStorage): void {
  try { storage.removeItem(STORAGE_KEY); } catch { /* No further persistence. */ }
}

export function readInvitation(storage: Storage = sessionStorage): string {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return "";
    const value = JSON.parse(raw);
    const age = Date.now() - value.savedAt;
    if (typeof value.savedAt !== "number" || !Number.isFinite(age) || age < 0 || age > MAX_AGE_MS) {
      throw new Error("expired");
    }
    return invitationToken(value.token);
  } catch {
    forgetInvitation(storage);
    return "";
  }
}

export function signinDestination(state: unknown): "/join" | "/" {
  return state !== null && typeof state === "object" &&
    "returnTo" in state && state.returnTo === "/join" ? "/join" : "/";
}

export const INVITATION_ERROR_COPY: Record<string, string> = {
  INVITE_INPUT_INVALID: "邀请内容不完整，请重新打开邀请链接。",
  INVITE_STORAGE_UNAVAILABLE: "浏览器无法暂存邀请，请登录后重新打开邀请链接。",
  INVALID_INVITE: "邀请无效或已过期，请联系邀请人重新生成。",
  INVITE_EXPIRED: "邀请已过期，请联系邀请人重新生成。",
  INVITE_REVOKED: "邀请已撤销，请联系服务商重新开通或生成邀请。",
  INVITE_NOT_FOUND: "邀请不存在，请联系邀请人确认。",
  INVITE_ALREADY_USED: "邀请已被使用。如果你已经加入，可进入工作台查看。",
  MEMBERSHIP_ALREADY_EXISTS: "你已经是该企业的成员，可进入工作台查看。",
  INVITE_IDENTITY_MISMATCH: "当前登录邮箱与受邀邮箱不同，请换用受邀账号登录。",
  OIDC_EMAIL_REQUIRED: "当前登录账号没有邮箱，请补全账号邮箱后重试。",
  OIDC_EMAIL_VERIFICATION_REQUIRED: "请先完成登录账号的邮箱验证，再接受邀请。",
};
