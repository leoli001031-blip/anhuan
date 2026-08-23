import { Button, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";

// OIDC 错误（如静默续期失败）必须可见且可重试，不能只有死按钮。
const AUTH_ERROR_COPY: Record<string, string> = {
  OIDC_SESSION_LOAD_FAILED: "登录状态读取失败，请重试登录。",
  OIDC_SESSION_RENEW_FAILED: "登录已过期，请重新登录。",
  OIDC_CALLBACK_FAILED: "登录回调失败，请重试。",
};

export default function Login() {
  const { login, authError } = useAuth();
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--eco-page-bg)",
      }}
    >
      <div
        style={{
          width: 360,
          padding: "40px 32px",
          background: "var(--eco-content-bg)",
          border: "1px solid var(--eco-border)",
          borderRadius: 8,
          textAlign: "center",
        }}
      >
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          安环智能助手
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          企业安环资料分析与问答
        </Typography.Paragraph>
        {authError && (
          <Typography.Paragraph type="danger" style={{ fontSize: 13 }}>
            {AUTH_ERROR_COPY[authError] ?? "登录遇到问题，请重试。"}
          </Typography.Paragraph>
        )}
        <Button type="primary" onClick={() => void login()} block>
          {authError ? "重试登录" : "登录"}
        </Button>
      </div>
    </div>
  );
}
