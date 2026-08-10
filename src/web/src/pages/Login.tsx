import { Button, Card, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";

export default function Login() {
  const { login } = useAuth();
  return (
    <div style={{ display: "flex", justifyContent: "center", marginTop: 80 }}>
      <Card style={{ width: 360, textAlign: "center" }}>
        <Typography.Title level={3}>安环平台</Typography.Title>
        <Typography.Paragraph type="secondary">
          F1 平台壳（Fixture 演示）
        </Typography.Paragraph>
        <Button type="primary" onClick={() => login()} block>
          通过 Keycloak 登录
        </Button>
      </Card>
    </div>
  );
}
