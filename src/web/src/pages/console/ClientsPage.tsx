// 运营台 · 客户企业列表：密集但易扫描，操作为文字链接。
import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Select, Table, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../../adapters";
import type { ClientAccount, ClientStage } from "../../adapters/types";
import { CLIENT_STAGE_LABEL } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";

export default function ClientsPage() {
  const api = useApi();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ClientAccount[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ name: string; stage: ClientStage }>();

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .listClients()
      .then((items) => {
        if (active) setRows(items);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, nonce]);

  const create = async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      const client = await api.createClient(values);
      message.success("客户已创建");
      setCreateOpen(false);
      form.resetFields();
      navigate(`/console/clients/${client.id}/materials`);
    } catch {
      message.error("创建失败，请重试");
    } finally {
      setCreating(false);
    }
  };

  if (error) {
    return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  }

  return (
    <div style={{ maxWidth: 960 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0 }}>
          客户企业
        </Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建客户
        </Button>
      </div>
      <Table<ClientAccount>
        rowKey="id"
        loading={rows === null}
        dataSource={rows ?? []}
        pagination={false}
        locale={{ emptyText: "暂无客户企业" }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (name: string, row) => (
              <Link to={`/console/clients/${row.id}/materials`}>{name}</Link>
            ),
          },
          {
            title: "阶段",
            dataIndex: "stage",
            width: 120,
            render: (stage: ClientStage) => CLIENT_STAGE_LABEL[stage] ?? stage,
          },
          {
            title: "更新时间",
            dataIndex: "updatedAt",
            width: 170,
            render: (iso: string) => (
              <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
            ),
          },
          {
            title: "操作",
            key: "actions",
            width: 160,
            render: (_, row) => (
              <span style={{ display: "flex", gap: 16 }}>
                <Link to={`/console/clients/${row.id}/materials`}>材料</Link>
                <Link to={`/console/clients/${row.id}/reports`}>报告</Link>
              </span>
            ),
          },
        ]}
      />
      <Modal
        title="新建客户"
        open={createOpen}
        onOk={() => void create()}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" initialValues={{ stage: "lead" }}>
          <Form.Item name="name" label="客户名称" rules={[{ required: true, message: "请输入客户名称" }]}>
            <Input placeholder="例如：蓝海化工有限公司" />
          </Form.Item>
          <Form.Item name="stage" label="阶段" rules={[{ required: true }]}>
            <Select
              options={(Object.keys(CLIENT_STAGE_LABEL) as ClientStage[]).map((s) => ({
                value: s,
                label: CLIENT_STAGE_LABEL[s],
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
