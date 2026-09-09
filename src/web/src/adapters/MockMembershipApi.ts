// Synthetic members only. The adapter factory's existing DEV flag owns selection.
import { ApiError } from "../api";
import type { BusinessMemberRole, MembershipApi, MembershipCommand, MembershipList } from "./MembershipApi";

export class MockMembershipApi implements MembershipApi {
  private readonly value: MembershipList;
  private readonly receipts = new Map<string, string>();
  constructor(enterpriseId: string) {
    this.value = {
      enterprise_id: enterpriseId, current_user_id: "10000000-0000-4000-8000-000000000001", can_manage: true,
      members: [
        {id: "20000000-0000-4000-8000-000000000001", user_id: "10000000-0000-4000-8000-000000000001", email: "admin@example.invalid", role: "enterprise_admin", status: "active"},
        {id: "20000000-0000-4000-8000-000000000002", user_id: "10000000-0000-4000-8000-000000000002", email: "colleague@example.invalid", role: "plant_admin", status: "active"},
        {id: "20000000-0000-4000-8000-000000000003", user_id: "10000000-0000-4000-8000-000000000003", email: "reviewer@example.invalid", role: "auditor", status: "revoked"},
        {id: "20000000-0000-4000-8000-000000000004", user_id: "10000000-0000-4000-8000-000000000004", email: "technical@example.invalid", role: "super_admin", status: "active"},
      ],
    };
  }
  currentRole(): BusinessMemberRole | null {
    const self = this.value.members.find(member => member.user_id === this.value.current_user_id);
    return self?.status === "active" && self.role !== "super_admin" ? self.role : null;
  }
  private snapshot(): MembershipList {
    const can_manage = this.currentRole() === "enterprise_admin";
    return {...this.value, can_manage, members: can_manage ? this.value.members.map(member => ({...member})) : []};
  }
  async listMemberships(): Promise<MembershipList> {
    if (this.currentRole() !== "enterprise_admin") throw new ApiError(404, "MEMBERSHIP_NOT_FOUND", false);
    return this.snapshot();
  }
  async changeMembership(id: string, command: MembershipCommand): Promise<MembershipList> {
    await this.listMemberships();
    const member = this.value.members.find(row => row.id === id);
    if (!member) throw new ApiError(404, "MEMBERSHIP_NOT_FOUND", false);
    const signature = JSON.stringify({id, action: command.action, role: command.action === "role" ? command.role : null});
    const prior = this.receipts.get(command.request_id);
    if (prior && prior !== signature) throw new ApiError(409, "MEMBERSHIP_REQUEST_CONFLICT", false);
    if (prior) return this.snapshot();
    if (member.role === "super_admin") throw new ApiError(409, "MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED", false);
    const removesAdmin = command.action === "revoke" || (command.action === "role" && command.role !== "enterprise_admin");
    if (removesAdmin && member.status === "active" && member.role === "enterprise_admin"
      && this.value.members.filter(row => row.role === "enterprise_admin" && row.status === "active").length === 1) {
      throw new ApiError(409, "MEMBERSHIP_LAST_ADMIN", false);
    }
    if (command.action === "role") member.role = command.role;
    else member.status = command.action === "revoke" ? "revoked" : "active";
    this.receipts.set(command.request_id, signature);
    return this.snapshot();
  }
}
