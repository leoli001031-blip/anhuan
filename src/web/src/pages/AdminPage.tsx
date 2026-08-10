import { useEffect, useState } from "react";
import { Button, Form, Input, Table, Tabs, Typography, message } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { api } from "../api";

interface Enterprise {
  id: string;
  name: string;
  license_no: string;
}
interface UserRow {
  id: string;
  keycloak_sub: string;
  email: string;
}

export default function AdminPage() {
  const { getAccessToken } = useAuth();
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);

  const refresh = () => {
    api<Enterprise[]>("/api/v1/enterprises", {
      token: getAccessToken(),
      enterpriseId: null,
    })
      .then(setEnterprises)
      .catch(() => setEnterprises([]));
    api<UserRow[]>("/api/v1/users", {
      token: getAccessToken(),
      enterpriseId: null,
    })
      .then(setUsers)
      .catch(() => setUsers([]));
  };

  useEffect(refresh, [getAccessToken]);

  const createEnterprise = async (values: { name: string; license_no: string }) => {
    try {
      await api("/api/v1/enterprises", {
        method: "POST",
        token: getAccessToken(),
        enterpriseId: null,
        body: values,
      });
      message.success("企业已创建");
      refresh();
    } catch (e) {
      message.error(String(e));
    }
  };

  return (
    <div>
      <Typography.Title level={4}>管理后台（super_admin）</Typography.Title>
      <Tabs
        items={[
          {
            key: "enterprises",
            label: "企业",
            children: (
              <>
                <Form onFinish={createEnterprise} layout="inline" style={{ marginBottom: 16 }}>
                  <Form.Item name="name" rules={[{ required: true }]}>
                    <Input placeholder="企业名称" />
                  </Form.Item>
                  <Form.Item name="license_no" rules={[{ required: true }]}>
                    <Input placeholder="许可证号" />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit">
                      创建企业
                    </Button>
                  </Form.Item>
                </Form>
                <Table<Enterprise>
                  rowKey="id"
                  dataSource={enterprises}
                  columns={[
                    { title: "名称", dataIndex: "name" },
                    { title: "许可证号", dataIndex: "license_no" },
                  ]}
                />
              </>
            ),
          },
          {
            key: "users",
            label: "用户",
            children: (
              <Table<UserRow>
                rowKey="id"
                dataSource={users}
                columns={[
                  { title: "邮箱", dataIndex: "email" },
                  { title: "Keycloak sub", dataIndex: "keycloak_sub" },
                ]}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
