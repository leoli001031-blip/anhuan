// 运营台 · 客户企业列表：桌面表格密集易扫描；<768px 切换为列表形态，
// 不逐字换行、不依赖横向拖动。页首给出客户上下文摘要，减少空壳感。
// 创建客户档案后进入详情；客户门户由详情中的独立开通动作管理。
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Form, Input, Modal, Select, Spin, Table, Typography, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { useApi, useSessionAccess, isMockData } from "../../adapters";
import type { ClientAccount, ClientStage } from "../../adapters/types";
import { CLIENT_STAGE_LABEL } from "../../adapters/types";
import { useAuth } from "../../auth/OidcProvider";
import { useAsyncContext } from "../../components/useAsyncContext";
import { clientCreationError, normalizeClientCreate } from "../../adapters/clientCreation";
import { pendingWrite, completePendingWrite } from "../../adapters/pendingWrites";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import { useNarrow } from "./useNarrow";

export default function ClientsPage() {
  const api = useApi();
  const navigate = useNavigate();
  const narrow = useNarrow();
  const {session, loading: sessionLoading, error: sessionError} = useSessionAccess();
  const {user, isInitializing} = useAuth();
  const context = JSON.stringify([session?.enterprise_id, user?.profile.sub, session?.product_role,
    session?.membership_role, sessionLoading, !!sessionError, !!isInitializing]);
  const current = useAsyncContext(context);
  const ready = !!session?.enterprise_id && !sessionLoading && !sessionError
    && (isMockData || (!!user?.profile.sub && !isInitializing));
  const canCreate = ready && session?.product_role === "provider_admin";
  const [record, setRecord] = useState<{context: string; rows: ClientAccount[]} | null>(null);
  const [failure, setFailure] = useState<{context: string; error: unknown} | null>(null);
  const rows = record?.context === context ? record.rows : null;
  const error = failure?.context === context ? failure.error : null;
  const [nonce, setNonce] = useState(0);
  const [createOpen, setCreateOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [form] = Form.useForm<{ name: string; stage: ClientStage }>();
  const flight = useRef<{context: string; active: boolean}>({context, active: false});
  const requestEpoch = useRef(0);

  useEffect(() => {
    flight.current = {context, active: false};
    setRecord(null); setFailure(null); setCreateOpen(null); setCreating(false); setCreateError(null);
    form.resetFields();
  }, [context, form]);
  useEffect(() => {
    if (!ready) return;
    const epoch = ++requestEpoch.current;
    let active = true;
    setFailure(null);
    api.listClients().then(items => {
      if (active && current() && epoch === requestEpoch.current) setRecord({context, rows: items});
    }).catch(error => {
      if (active && current() && epoch === requestEpoch.current) setFailure({context, error});
    });
    return () => { active = false; };
    // The render identity and request epoch own this read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, context, ready, nonce]);

  const create = async () => {
    if (!canCreate || !current() || (flight.current.context === context && flight.current.active)) return;
    const invocation = {context, active: true}; flight.current = invocation;
    setCreating(true); setCreateError(null);
    try {
      let values: {name: string; stage: ClientStage};
      try { values = await form.validateFields(); } catch { return; }
      if (!current() || !canCreate) return;
      const normalized = normalizeClientCreate({display_name: values.name, stage: values.stage});
      const signature = JSON.stringify(normalized);
      const pending = await pendingWrite("crm.account.create", session!.enterprise_id,
        user?.profile.sub ?? "mock-user", signature);
      if (!current() || !canCreate) return;
      const requestId = pending.requestId;
      ++requestEpoch.current;
      const client = await api.createClient({name: normalized.display_name, stage: normalized.stage, requestId});
      if (!current()) return;
      completePendingWrite(pending);
      message.success("客户已创建");
      setCreateOpen(null); form.resetFields();
      navigate(`/console/clients/${client.id}`);
    } catch (error) {
      if (current()) setCreateError(clientCreationError(error));
    } finally {
      invocation.active = false;
      if (current()) setCreating(false);
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
        {canCreate && (
          <Button type="primary" onClick={() => setCreateOpen(context)}>
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
        open={createOpen === context}
        onOk={() => void create()}
        onCancel={() => { if (!creating) setCreateOpen(null); }}
        closable={!creating}
        maskClosable={!creating}
        keyboard={!creating}
        cancelButtonProps={{disabled: creating}}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        {createError && <Alert type="error" showIcon message={createError} style={{marginBottom: 16}} />}
        <Typography.Paragraph type="secondary">创建后可管理客户材料与报告，并在详情中开通客户门户。</Typography.Paragraph>
        <Form form={form} layout="vertical" disabled={creating} initialValues={{ stage: "lead" }}>
          <Form.Item name="name" label="客户名称" rules={[{ required: true, whitespace: true, message: "请输入客户名称" }, {max: 200, message: "客户名称不能超过 200 字" }]}>
            <Input aria-label="客户名称" placeholder="例如：蓝海化工有限公司" />
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
