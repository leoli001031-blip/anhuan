import { ApiError, tenantFetch } from "../api";

export const BUSINESS_MEMBER_ROLES = ["enterprise_admin", "plant_admin", "auditor", "partner"] as const;
export type BusinessMemberRole = typeof BUSINESS_MEMBER_ROLES[number];
export type MemberRole = BusinessMemberRole | "super_admin";
export type MembershipAction = "role" | "revoke" | "restore";
export interface ManagedMember {
  id: string;
  user_id: string;
  email: string;
  role: MemberRole;
  status: "active" | "revoked";
}
export interface MembershipList {
  enterprise_id: string;
  current_user_id: string;
  can_manage: boolean;
  members: ManagedMember[];
}
export type MembershipCommand =
  | { action: "role"; request_id: string; role: BusinessMemberRole }
  | { action: "revoke" | "restore"; request_id: string };
export interface MembershipApi {
  listMemberships(): Promise<MembershipList>;
  changeMembership(id: string, command: MembershipCommand): Promise<MembershipList>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const roles = new Set<string>([...BUSINESS_MEMBER_ROLES, "super_admin"]);
function invalid(): never { throw new ApiError(0, "MEMBERSHIP_RECEIPT_INVALID", false); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
  return value;
}
export function parseMembershipList(raw: unknown): MembershipList {
  const value = object(raw);
  const enterprise_id = uuid(value.enterprise_id);
  const current_user_id = uuid(value.current_user_id);
  if (typeof value.can_manage !== "boolean" || !Array.isArray(value.members)) invalid();
  const ids = new Set<string>(), users = new Set<string>();
  const members = value.members.map((rawMember): ManagedMember => {
    const member = object(rawMember);
    const id = uuid(member.id), user_id = uuid(member.user_id);
    if (ids.has(id) || users.has(user_id) || typeof member.email !== "string" || !member.email
      || typeof member.role !== "string" || !roles.has(member.role)
      || (member.status !== "active" && member.status !== "revoked")) invalid();
    ids.add(id); users.add(user_id);
    return {id, user_id, email: member.email, role: member.role as MemberRole, status: member.status};
  });
  if ((!value.can_manage && members.length > 0) || (value.can_manage && !members.some(member =>
    member.user_id === current_user_id && member.role === "enterprise_admin" && member.status === "active"))) invalid();
  return {enterprise_id, current_user_id, can_manage: value.can_manage, members};
}

export class HttpMembershipApi implements MembershipApi {
  private readonly getToken: () => string | null;
  constructor(getToken: () => string | null) { this.getToken = getToken; }
  private async request(path: string, body?: unknown): Promise<MembershipList> {
    const result = await tenantFetch(path, {token: this.getToken(), method: body ? "POST" : "GET", body});
    const value = parseMembershipList(result.payload);
    if (!result.enterpriseId || result.enterpriseId !== value.enterprise_id) {
      throw new ApiError(403, "MEMBERSHIP_ENTERPRISE_MISMATCH", false);
    }
    return value;
  }
  listMemberships(): Promise<MembershipList> { return this.request("/v1/memberships"); }
  changeMembership(id: string, command: MembershipCommand): Promise<MembershipList> {
    uuid(id); uuid(command.request_id);
    if (!["role", "revoke", "restore"].includes(command.action)
      || (command.action === "role" && !BUSINESS_MEMBER_ROLES.includes(command.role))) {
      throw new ApiError(409, "MEMBERSHIP_ROLE_INVALID", false);
    }
    return this.request(`/v1/memberships/${encodeURIComponent(id)}/${command.action}`, {
      request_id: command.request_id,
      ...(command.action === "role" ? {role: command.role} : {}),
    });
  }
}
