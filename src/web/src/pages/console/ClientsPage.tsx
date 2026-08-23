// 运营台 · 客户企业列表：桌面表格密集易扫描；<768px 切换为列表形态，
// 不逐字换行、不依赖横向拖动。页首给出客户上下文摘要，减少空壳感。
// 正式 HTTP 无完整 audience 开通合同：隐藏「新建客户」（仅演示环境可见）。
import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Select, Spin, Table, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { useApi, isMockData } from "../../adapters";
import type { ClientAccount, ClientStage } from "../../adapters/types";
import { CLIENT_STAGE_LABEL } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import { useNarrow } from "./useNarrow";

export default function ClientsPage() {
  const api = useApi();
  const navigate = useNavigate();
  const narrow = useNarrow();
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
      navigate(`/console/clients/${client.id}`);
    } catch {
      message.error("创建失败，请重试");
    } finally {
      setCreating(false);
    }
  };

  if (error) {
    return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  }

  const activeCount = (rows ?? []).filter((r) => r.stage === "active").length;

  return (
    <main className="console-page clients-page">
      <div className="console-page__header">
        <Typography.Title level={2}>
          客户企业
        </Typography.Title>
        {isMockData && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建客户
          </Button>
        )}
      </div>
      <Typography.Paragraph type="secondary" className="console-page__subtitle">
        {rows === null
          ? "正在加载客户列表…"
          : `共 ${rows.length} 家客户 · 服务中 ${activeCount} 家。进入客户后可管理材料与报告。`}
      </Typography.Paragraph>
      {narrow ? (
        rows === null ? (
          <Spin style={{ display: "block", margin: "48px auto" }} />
        ) : rows.length === 0 ? (
          <Typography.Text type="secondary">暂无客户企业</Typography.Text>
        ) : (
          <div>
            {rows.map((c) => (
              <div key={c.id} className="client-mobile-item">
                <Link to={`/console/clients/${c.id}`} style={{ fontSize: 15 }}>
                  {c.name}
                </Link>
                <div className="client-mobile-meta">
                  {CLIENT_STAGE_LABEL[c.stage] ?? c.stage} · 更新于 {formatDateTime(c.updatedAt)}
                </div>
                <div className="client-mobile-actions">
                  <Link to={`/console/clients/${c.id}/materials`}>材料</Link>
                  <Link to={`/console/clients/${c.id}/reports`}>报告</Link>
                </div>
              </div>
            ))}
          </div>
        )
      ) : (
        <Table<ClientAccount>
          className="clients-table"
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
                <Link to={`/console/clients/${row.id}`}>{name}</Link>
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
      )}
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
    </main>
  );
}
