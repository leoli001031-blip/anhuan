import { useEffect, useRef, useState } from "react";
import { Alert, Button, Select, Space, Spin, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { useApi, useSessionAccess } from "../adapters";
import { homePathFor } from "../adapters/SessionAccess";
import { ApiError } from "../adapters/errors";
import { BUSINESS_MEMBER_ROLES, type BusinessMemberRole, type ManagedMember, type MembershipAction, type MembershipCommand, type MembershipList } from "../adapters/MembershipApi";
import { useAuth } from "../auth/OidcProvider";
import { useAsyncContext } from "../components/useAsyncContext";
import MockBadge from "../components/MockBadge";
import "./MembersPage.css";

const ERROR_COPY: Record<string, string> = {
  MEMBERSHIP_NOT_FOUND: "成员不存在，或你没有本企业的成员管理权限。",
  MEMBERSHIP_LAST_ADMIN: "请先保留另一位在用的企业管理员，再调整这位成员。",
  MEMBERSHIP_REQUEST_CONFLICT: "这次操作的内容与先前请求不一致，请刷新成员列表后重新操作。",
  MEMBERSHIP_ROLE_INVALID: "该职责不可设置，请选择列表中的业务职责。",
  MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED: "技术管理员由专用管理入口维护，不能在这里调整。",
  MEMBERSHIP_UNAVAILABLE: "成员服务暂时不可用，请稍后重试。",
  MEMBERSHIP_RECEIPT_INVALID: "未能确认成员状态，请刷新列表后重试。",
  MEMBERSHIP_ENTERPRISE_MISMATCH: "企业上下文已变化，请刷新列表后重试。",
};
function errorCopy(error: unknown): string {
  const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  return ERROR_COPY[code] ?? "操作未完成，请刷新查看成员状态，或重试这次操作。";
}

export default function MembersPage() {
  const api = useApi();
  const {session, loading, error: sessionError, reload} = useSessionAccess();
  const {user} = useAuth();
  const enterpriseId = session?.enterprise_id ?? "";
  const context = `${enterpriseId}:${user?.profile.sub ?? ""}:${session?.membership_role ?? ""}:${loading ? "loading" : "ready"}`;
  const current = useAsyncContext(context);
  const [record, setRecord] = useState<{context: string; value: MembershipList} | null>(null);
  const [failure, setFailure] = useState<{context: string; message: string} | null>(null);
  const [notice, setNotice] = useState<{context: string; message: string} | null>(null);
  const [drafts, setDrafts] = useState<Record<string, BusinessMemberRole>>({});
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const requestEpoch = useRef(0);
  const flight = useRef<{context: string; active: boolean}>({context, active: false});
  const retry = useRef<{signature: string; id: string} | null>(null);
  const value = record?.context === context ? record.value : null;
  const failureText = failure?.context === context ? failure.message : null;
  const noticeText = notice?.context === context ? notice.message : null;
  const ready = !!enterpriseId && !loading && !sessionError;

  function accept(receipt: MembershipList) {
    if (receipt.enterprise_id !== enterpriseId) throw new ApiError(403, "MEMBERSHIP_ENTERPRISE_MISMATCH", false);
    setRecord({context, value: receipt});
    setDrafts({});
  }
  useEffect(() => {
    requestEpoch.current += 1;
    flight.current = {context, active: false}; retry.current = null;
    setRecord(null); setFailure(null); setNotice(null); setDrafts({}); setBusy(false);
  }, [context]);
  useEffect(() => {
    if (!ready) return;
    const epoch = ++requestEpoch.current;
    let active = true;
    setFailure(null);
    api.listMemberships()
      .then(receipt => { if (active && current() && epoch === requestEpoch.current) accept(receipt); })
      .catch(error => {
        if (active && current() && epoch === requestEpoch.current) {
          setRecord(null); setFailure({context, message: errorCopy(error)});
        }
      });
    return () => { active = false; };
    // The render identity owns the callback; an operation invalidates earlier reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, context, ready, refresh]);

  async function command(member: ManagedMember, action: MembershipAction, role?: BusinessMemberRole) {
    if (!ready || !current() || !value?.can_manage || member.role === "super_admin"
      || (flight.current.context === context && flight.current.active)) return;
    if (action === "role" && (!role || !BUSINESS_MEMBER_ROLES.includes(role))) return;
    const epoch = ++requestEpoch.current;
    const invocation = {context, active: true}; flight.current = invocation;
    setBusy(true); setFailure(null); setNotice(null);
    const signature = JSON.stringify({context, id: member.id, action, role: action === "role" ? role : null});
    if (retry.current?.signature !== signature) retry.current = {signature, id: crypto.randomUUID()};
    const request: MembershipCommand = action === "role"
      ? {action, request_id: retry.current.id, role: role!}
      : {action, request_id: retry.current.id};
    try {
      const receipt = await api.changeMembership(member.id, request);
      if (!current() || epoch !== requestEpoch.current) return;
      accept(receipt);
      retry.current = null;
      setNotice({context, message: action === "role" ? "成员职责已更新。" : action === "revoke" ? "成员已停用。" : "成员已恢复。"});
      if (!receipt.can_manage) reload();
    } catch (error) {
      if (current() && epoch === requestEpoch.current) setFailure({context, message: errorCopy(error)});
    } finally {
      invocation.active = false;
      if (current() && epoch === requestEpoch.current) setBusy(false);
    }
  }

  const roleLabels = {
    enterprise_admin: "企业管理员",
    plant_admin: session?.product_role === "client_user" ? "协作成员" : "顾问",
    auditor: session?.product_role === "client_user" ? "审核成员" : "专家",
    partner: "合作成员",
    super_admin: "技术管理员",
  };
  return <main className="members-page">
    <Link to={session ? homePathFor(session.product_role) : "/"}>返回工作台</Link>
    <div className="members-page__heading">
      <div>
        <Typography.Title level={2}>本企业成员</Typography.Title>
        <Typography.Paragraph type="secondary">调整成员职责或暂停访问。停用后保留历史业务记录。</Typography.Paragraph>
      </div>
      <Button disabled={busy || loading} onClick={() => {
        setNotice(null);
        if (sessionError || !session) reload();
        else setRefresh(n => n + 1);
      }}>刷新成员列表</Button>
    </div>
    {value?.can_manage === false && <Alert type="info" showIcon message="你当前没有成员管理权限。" />}
    {value?.can_manage !== false && loading && <Spin />}
    {!!sessionError && <Alert type="error" showIcon message="无法确认当前企业身份，请刷新重试。" />}
    {ready && failureText && <Alert type="error" showIcon message={failureText} />}
    {ready && noticeText && value?.can_manage && <Alert type="success" showIcon message={noticeText} />}
    {ready && !value && !failureText && <Spin />}
    {ready && value?.can_manage && <>
      <Typography.Paragraph type="secondary" className="members-page__count">共 {value.members.length} 位成员 · 在用 {value.members.filter(member => member.status === "active").length} 位</Typography.Paragraph>
      <div className="members-list" aria-label="企业成员列表">
        {value.members.map(member => {
          const protectedMember = member.role === "super_admin";
          const selected = drafts[member.id] ?? member.role;
          return <section key={member.id} className="members-row" aria-label={member.email}>
            <div className="members-row__identity">
              <Typography.Text strong>{member.email}</Typography.Text>
              <Space size={8} wrap>
                {member.user_id === value.current_user_id && <Typography.Text type="secondary">当前账号</Typography.Text>}
                <Tag color={member.status === "active" ? "green" : undefined}>{member.status === "active" ? "在用" : "已停用"}</Tag>
              </Space>
            </div>
            {protectedMember ? <Typography.Text type="secondary">技术管理员 · 专用入口维护</Typography.Text> : <div className="members-row__actions">
              <Select aria-label={`${member.email}的职责`} value={selected} disabled={busy}
                options={BUSINESS_MEMBER_ROLES.map(role => ({value: role, label: roleLabels[role]}))}
                onChange={(role: BusinessMemberRole) => setDrafts(previous => ({...previous, [member.id]: role}))} />
              <Button disabled={busy || selected === member.role} onClick={() => void command(member, "role", selected as BusinessMemberRole)}>保存职责</Button>
              <Button danger={member.status === "active"} disabled={busy}
                onClick={() => void command(member, member.status === "active" ? "revoke" : "restore")}>{member.status === "active" ? "停用成员" : "恢复成员"}</Button>
            </div>}
          </section>;
        })}
      </div>
    </>}
    <MockBadge />
  </main>;
}
