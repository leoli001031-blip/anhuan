import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Grid,
  List,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import CrmAccountModal from "../components/CrmAccountModal";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import { crmStageCopy, formatP4DateTime, stageColor } from "../reasonCopy";
import type { CreateCrmAccountInput, CrmAccount, CrmAccountCollection } from "../types";
import {
  createCrmAccount,
  isViewsReportsRequestAborted,
  listCrmAccounts,
  userFacingViewsReportsError,
} from "../viewsReportsApi";

const EMPTY_ACCOUNTS: CrmAccountCollection = { items: [], allowed_actions: [] };

export default function CrmAccountListPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => listCrmAccounts(token, signal),
    [],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP4TenantQuery(EMPTY_ACCOUNTS, load);

  useEffect(() => setCreateOpen(false), [tenantEpoch]);

  const handleCreate = async (input: CreateCrmAccountInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createCrmAccount(token, input, signal),
      );
      setCreateOpen(false);
      navigate("/crm/" + created.id);
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const columns: TableColumnsType<CrmAccount> = [
    {
      title: "内部客户档案",
      dataIndex: "display_name",
      width: 260,
      fixed: "left",
      render: (value: string, account) =>
        account.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/crm/" + account.id)}>{value}</Button>
        ) : value,
    },
    {
      title: "阶段",
      dataIndex: "stage",
      width: 110,
      render: (value: string) => <Tag color={stageColor(value)}>{crmStageCopy(value)}</Tag>,
    },
    {
      title: "负责人 ID",
      dataIndex: "owner_user_id",
      width: 220,
      ellipsis: true,
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "联系人",
      dataIndex: "contact_count",
      width: 90,
      render: (value: number | undefined) => value ?? "—",
    },
    {
      title: "下次跟进",
      dataIndex: "next_follow_up_at",
      width: 180,
      render: formatP4DateTime,
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 180,
      render: formatP4DateTime,
    },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, account) =>
        account.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/crm/" + account.id)}>查看</Button>
        ) : null,
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>内部客户档案</Typography.Title>
          <Typography.Text type="secondary">维护合成客户、联系人与 append-only 人工跟进</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("create") && (
            <Button type="primary" onClick={() => setCreateOpen(true)}>新建客户档案</Button>
          )}
        </Space>
      </Space>

      <P4BoundaryBanner />

      {error && (
        <Alert
          type="error"
          showIcon
          message="客户档案操作未完成"
          description={error}
          action={<Button onClick={() => void reload()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 320, display: "grid", placeItems: "center" }}>
          <Spin tip="正在加载客户档案" />
        </div>
      ) : data.items.length === 0 && !error ? (
        <Empty description="当前企业尚无内部客户档案">
          {data.allowed_actions.includes("create") && (
            <Button type="primary" onClick={() => setCreateOpen(true)}>建立第一条档案</Button>
          )}
        </Empty>
      ) : screens.md ? (
        <Table<CrmAccount>
          rowKey="id"
          dataSource={data.items}
          columns={columns}
          pagination={false}
          scroll={{ x: 1130 }}
        />
      ) : (
        <List
          dataSource={data.items}
          renderItem={(account) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Typography.Text strong>{account.display_name}</Typography.Text>
                  <Tag color={stageColor(account.stage)}>{crmStageCopy(account.stage)}</Tag>
                </Space>
                <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>
                  下次跟进：{formatP4DateTime(account.next_follow_up_at)}
                </Typography.Paragraph>
                {account.allowed_actions.includes("view") && (
                  <Button block onClick={() => navigate("/crm/" + account.id)}>查看档案</Button>
                )}
              </div>
            </List.Item>
          )}
        />
      )}

      <CrmAccountModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={(input) => handleCreate(input as CreateCrmAccountInput)}
      />
    </div>
  );
}
