import { useState } from "react";
import { Button, Card, Form, Input, Select, Typography, message } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { api } from "../api";

const ROLES = ["enterprise_admin", "plant_admin", "partner", "auditor"];

export default function InvitePage() {
  const { getAccessToken } = useAuth();
  const [token, setToken] = useState<string | null>(null);

  const create = async (values: { email: string; role: string }) => {
    try {
      const resp = await api<{ token: string }>("/api/v1/invitations", {
        method: "POST",
        token: getAccessToken(),
        body: { email: values.email, role: values.role },
      });
      setToken(resp.token);
      message.success("邀请已创建");
    } catch (e) {
      message.error(String(e));
    }
  };

  const consume = async (values: { token: string }) => {
    try {
      await api("/api/v1/invitations/consume", {
        method: "POST",
        token: getAccessToken(),
        body: {
          token: values.token,
          keycloak_sub: "",
          email: "",
        },
      });
      message.success("邀请已消费");
    } catch (e) {
      message.error(String(e));
    }
  };

  return (
    <div style={{ maxWidth: 560 }}>
      <Typography.Title level={4}>邀请</Typography.Title>
      <Card title="创建邀请" style={{ marginBottom: 16 }}>
        <Form onFinish={create} layout="vertical">
          <Form.Item name="email" label="邮箱" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select
              options={ROLES.map((r) => ({ value: r, label: r }))}
              placeholder="选择角色"
            />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            创建
          </Button>
        </Form>
        {token && (
          <Typography.Paragraph style={{ marginTop: 12 }} copyable>
            邀请链接：{token}
          </Typography.Paragraph>
        )}
      </Card>
      <Card title="消费邀请">
        <Form onFinish={consume} layout="vertical">
          <Form.Item name="token" label="邀请 token" rules={[{ required: true }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            消费
          </Button>
        </Form>
      </Card>
    </div>
  );
}
