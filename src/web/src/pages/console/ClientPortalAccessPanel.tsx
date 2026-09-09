import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Space, Spin, Tag, Typography } from "antd";
import { api } from "../../api";
import { useAuth } from "../../auth/OidcProvider";
import { useSessionAccess } from "../../adapters";
import { useAsyncContext } from "../../components/useAsyncContext";
import { invitationLink } from "../../features/invitations/invitationFlow";

type Access = {
  client_id: string;
  status: "not_open" | "active" | "revoked";
  members: Array<{ id: string; email: string; role: string }>;
  invitations: Array<{ email: string; status: "pending" | "accepted" | "expired" | "revoked" }>;
};
const COPY: Record<string,string> = {
  CLIENT_PORTAL_RESTORE_REQUIRED: "请先恢复门户访问，再生成邀请。",
  CLIENT_PORTAL_REQUEST_CONFLICT: "这次操作的内容已变更，请刷新状态后重新操作。",
  CLIENT_PORTAL_ORGANIZATION_UNCONFIGURED: "客户组织身份尚未配置，请联系系统管理员。",
  CLIENT_PORTAL_INVITE_FINISHED: "这条邀请已结束，请刷新状态后生成新邀请。",
  CLIENT_PORTAL_NOT_FOUND: "客户不存在，或当前账号没有管理权限。",
};
function errorCopy(error: unknown): string {
  const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  return COPY[code] ?? "操作未完成。请刷新查看当前状态，或重试这次操作。";
}

export default function ClientPortalAccessPanel({clientId}: {clientId: string}) {
  const {session} = useSessionAccess();
  const {getAccessToken} = useAuth();
  const context = `${session?.enterprise_id ?? ""}:${clientId}`;
  const current = useAsyncContext(context);
  const [record,setRecord] = useState<{context: string; value: Access} | null>(null);
  const [failure,setFailure] = useState<{context: string; message: string} | null>(null);
  const [issued,setIssued] = useState<{context: string; link: string; email: string} | null>(null);
  const [license,setLicense] = useState("");
  const [email,setEmail] = useState("");
  const [busy,setBusy] = useState(false);
  const [refresh,setRefresh] = useState(0);
  const flight = useRef<{context: string; active: boolean}>({context,active:false});
  const retry = useRef<{signature: string; id: string} | null>(null);
  const requestEpoch = useRef(0);
  const base = `/v1/clients/${encodeURIComponent(clientId)}`;
  const value = record?.context===context ? record.value : null;
  const failureText = failure?.context===context ? failure.message : null;
  const link = issued?.context===context ? issued : null;

  function acceptAccess(value: Access) {
    if (value?.client_id !== clientId || !["not_open","active","revoked"].includes(value.status)
        || !Array.isArray(value.members) || !Array.isArray(value.invitations)) throw new Error("INVALID_PORTAL_RECEIPT");
    setRecord({context,value});
  }
  useEffect(() => {
    flight.current={context,active:false}; retry.current=null;
    setRecord(null);setFailure(null);setIssued(null);setLicense("");setEmail("");setBusy(false);
  },[context]);
  useEffect(() => {
    let active=true;
    const epoch=++requestEpoch.current;
    setFailure(null);
    api<Access>(base+"/portal-access",{token:getAccessToken()})
      .then(value=>{if(active && current() && epoch===requestEpoch.current) acceptAccess(value);})
      .catch(error=>{if(active && current() && epoch===requestEpoch.current)setFailure({context,message:errorCopy(error)});});
    return ()=>{active=false;};
    // The tenant/route identity owns each request; refresh explicitly reloads it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[context,refresh]);

  async function command(action: "open" | "revoke" | "restore" | "invite") {
    if (!current() || (flight.current.context===context && flight.current.active)) return;
    const invocation={context,active:true};flight.current=invocation;
    // A write invalidates every earlier refresh in this same client context.
    const epoch=++requestEpoch.current;
    setBusy(true);setFailure(null);setIssued(null);
    const details = action==="open" ? {license_no:license.trim()} : action==="invite" ? {email:email.trim().toLowerCase()} : {};
    const signature=JSON.stringify({context,action,...details});
    if (retry.current?.signature!==signature) retry.current={signature,id:crypto.randomUUID()};
    const path=action==="invite" ? "/portal-invitations" : "/portal-access"+(action==="open" ? "" : `/${action}`);
    try {
      const result=await api<Access | {token: string; email: string}>(base+path,{
        method:"POST",token:getAccessToken(),body:{request_id:retry.current.id,...details},
      });
      if (!current() || epoch!==requestEpoch.current) return;
      if (action==="invite") {
        if (!("token" in result) || result.email!==email.trim().toLowerCase()) throw new Error("INVALID_PORTAL_INVITATION");
        setIssued({context,email:result.email,link:invitationLink(result.token,window.location.origin)});
        setRefresh(n=>n+1);
      } else acceptAccess(result as Access);
      retry.current=null;
    } catch(error) {
      if(current() && epoch===requestEpoch.current)setFailure({context,message:errorCopy(error)});
    } finally {
      invocation.active=false;
      if(current())setBusy(false);
    }
  }

  return <section style={{marginTop:32,paddingTop:24,borderTop:"1px solid var(--eco-border)"}} aria-label="客户门户访问">
    <Typography.Title level={4}>客户门户访问</Typography.Title>
    {failureText && <Alert type="error" showIcon message={failureText} style={{marginBottom:12}} />}
    {!value && !failureText && <Spin />}
    {value && <>
      <Tag color={value.status==="active" ? "green" : undefined}>{value.status==="active" ? "已开通" : value.status==="revoked" ? "已暂停" : "未开通"}</Tag>
      {value.status==="not_open" && <Space wrap style={{marginTop:12}}>
        <Input aria-label="客户统一社会信用代码" placeholder="客户统一社会信用代码" value={license} maxLength={64} disabled={busy} onChange={e=>setLicense(e.target.value)} />
        <Button type="primary" disabled={busy || !license.trim()} loading={busy} onClick={()=>void command("open")}>开通客户门户</Button>
      </Space>}
      {value.status==="revoked" && <div style={{marginTop:12}}>
        <Typography.Paragraph type="secondary">客户目前无法访问对客资料。恢复后，已加入的成员可继续使用；旧邀请仍然失效。</Typography.Paragraph>
        <Button disabled={busy} onClick={()=>void command("restore")}>恢复访问</Button>
      </div>}
      {value.status==="active" && <>
        <Typography.Paragraph style={{marginTop:12}}>已加入 {value.members.length} 位成员。邀请链接有效期为24小时，只能由对应邮箱的登录账号使用一次。</Typography.Paragraph>
        <Space wrap>
          <Input aria-label="客户负责人邮箱" type="email" placeholder="客户负责人邮箱" value={email} disabled={busy} onChange={e=>setEmail(e.target.value)} />
          <Button type="primary" disabled={busy || !/^[^\s@]+@[^\s@]+$/.test(email.trim())} loading={busy} onClick={()=>void command("invite")}>生成负责人邀请</Button>
          <Button danger disabled={busy} onClick={()=>void command("revoke")}>暂停门户访问</Button>
        </Space>
      </>}
      {value.members.map(member=><Typography.Paragraph key={member.id} style={{marginTop:8,marginBottom:0}}>{member.email}</Typography.Paragraph>)}
      {value.invitations.length>0 && <Typography.Paragraph type="secondary" style={{marginTop:12}}>最近邀请：{value.invitations.map(item=>`${item.email}（${({pending:"待接受",accepted:"已接受",expired:"已过期",revoked:"已撤销"})[item.status]}）`).join("、")}</Typography.Paragraph>}
    </>}
    {link && <Alert type="success" style={{marginTop:12}} message={`已为 ${link.email} 生成邀请`} description={<Typography.Paragraph copyable={{text:link.link}} style={{marginBottom:0,wordBreak:"break-all"}}>{link.link}</Typography.Paragraph>} />}
    <Button type="link" disabled={busy} onClick={()=>{retry.current=null;setIssued(null);setRefresh(n=>n+1);}}>刷新访问状态</Button>
  </section>;
}
