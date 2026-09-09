import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Space, Spin, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/OidcProvider";
import { api, invalidateTenantContext, setSelectedEnterprise } from "../api";
import { useAsyncContext } from "../components/useAsyncContext";
import { forgetInvitation, invitationToken, INVITATION_ERROR_COPY,
  readInvitation, rememberInvitation } from "../features/invitations/invitationFlow";

export default function JoinPage() {
  const { user, isAuthenticated, isInitializing, login, logout, getAccessToken } = useAuth();
  const navigate = useNavigate();
  const isCurrent = useAsyncContext(user?.profile.sub ?? "anonymous");
  const [token, setToken] = useState(readInvitation);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);

  useEffect(() => { setBusy(false); submitting.current = false; setError(null); }, [user?.profile.sub]);

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (!fragment.has("invite")) return;
    // Remove the credential before any navigation or external sign-in.
    window.history.replaceState(null, "", window.location.pathname);
    try {
      const next = invitationToken(fragment.get("invite") ?? "");
      setToken(next);
      // A storage failure still allows acceptance by an already logged-in user.
      try { rememberInvitation(next); } catch { /* login() checks persistence */ }
    } catch { setError("INVITE_INPUT_INVALID"); }
  }, []);

  const accept = async () => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const input = invitationToken(token);
      if (!isAuthenticated) {
        rememberInvitation(input);
        await login("/join");
        return;
      }
      const receipt = await api<{ enterprise_id: string }>("/v1/invitations/consume", {
        method: "POST", token: getAccessToken(), enterpriseId: null,
        body: { token: input },
      });
      if (!isCurrent()) return;
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(receipt.enterprise_id)) {
        throw new Error("INVITE_RECEIPT_INVALID");
      }
      forgetInvitation();
      // A server-issued result is a selection hint. ApiProvider reloads actual
      // memberships and validates the selected tenant before any business call.
      setSelectedEnterprise(receipt.enterprise_id);
      navigate("/", { replace: true });
    } catch (caught) {
      if (isCurrent()) setError(caught instanceof Error ? caught.message : "NETWORK_ERROR");
    } finally {
      if (isCurrent()) { setBusy(false); submitting.current = false; }
    }
  };

  const switchAccount = async () => {
    try { rememberInvitation(token); await logout(); }
    catch { if (isCurrent()) setError("INVITE_STORAGE_UNAVAILABLE"); }
  };

  if (isInitializing) return <Spin fullscreen tip="正在检查登录状态" />;
  return <main style={{ maxWidth: 560, margin: "64px auto", padding: 20 }}>
    <Card>
      <Typography.Title level={3}>接受企业邀请</Typography.Title>
      <Typography.Paragraph>请使用收到邀请的邮箱登录。接受后即可加入对应企业。</Typography.Paragraph>
      {isAuthenticated && <Typography.Paragraph>当前账号：{user?.profile.email ?? "未设置邮箱"}</Typography.Paragraph>}
      <Input.TextArea aria-label="邀请内容" value={token} rows={3} disabled={busy}
        placeholder="打开邀请链接后会自动填入，也可粘贴邀请内容"
        onChange={(event) => { setToken(event.target.value); setError(null); }} />
      {error && <Alert style={{ marginTop: 16 }} type="error" showIcon
        message={INVITATION_ERROR_COPY[error] ?? "暂时无法确认加入结果，请重试或进入工作台检查。"} />}
      <Space wrap style={{ marginTop: 20 }}>
        <Button type="primary" loading={busy} disabled={!token.trim()} onClick={() => void accept()}>
          {isAuthenticated ? "接受邀请并进入" : "登录并继续"}
        </Button>
        {isAuthenticated && <Button disabled={busy} onClick={() => { forgetInvitation(); invalidateTenantContext(); navigate("/"); }}>进入工作台</Button>}
        {isAuthenticated && <Button disabled={busy} onClick={() => void switchAccount()}>更换账号</Button>}
      </Space>
    </Card>
  </main>;
}
