import { Button, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";

export default function Login() {
  const { login } = useAuth();
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
        <Button type="primary" onClick={() => void login()} block>
          登录
        </Button>
      </div>
    </div>
  );
}
