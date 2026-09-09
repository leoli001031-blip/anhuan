import { useEffect, useRef, useState } from "react";
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
import { useSessionAccess } from "../../../adapters";
import { useAuth } from "../../../auth/OidcProvider";
import { useAsyncContext } from "../../../components/useAsyncContext";
import { clientCreationError, normalizeClientCreate } from "../../../adapters/clientCreation";
import { pendingWrite, completePendingWrite } from "../../../adapters/pendingWrites";
import { crmStageCopy, formatP4DateTime, stageColor } from "../reasonCopy";
import type { CreateCrmAccountInput, CrmAccount, CrmAccountCollection } from "../types";
import {
  createCrmAccount,
  listCrmAccounts,
  userFacingViewsReportsError,
} from "../viewsReportsApi";

const EMPTY_ACCOUNTS: CrmAccountCollection = { items: [], allowed_actions: [] };

export default function CrmAccountListPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const {session, loading: sessionLoading, error: sessionError} = useSessionAccess();
  const {user, getAccessToken, isInitializing} = useAuth();
  const context = JSON.stringify([session?.enterprise_id, user?.profile.sub, session?.product_role,
    session?.membership_role, sessionLoading, !!sessionError, !!isInitializing]);
  const current = useAsyncContext(context);
  const ready = !!session?.enterprise_id && !!user?.profile.sub && !sessionLoading && !sessionError && !isInitializing;
  const [record, setRecord] = useState<{context: string; data: CrmAccountCollection} | null>(null);
  const [failure, setFailure] = useState<{context: string; message: string} | null>(null);
  const [createOpen, setCreateOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const data = record?.context === context ? record.data : EMPTY_ACCOUNTS;
  const error = failure?.context === context ? failure.message : null;
  const loading = ready && record?.context !== context && !error;
  const canCreate = ready && session?.product_role === "provider_admin" && data.allowed_actions.includes("create");
  const requestEpoch = useRef(0);
  const flight = useRef<{context: string; active: boolean}>({context, active: false});
  const reload = () => setRefresh(value => value + 1);
  useEffect(() => {
    flight.current = {context, active: false};
    setRecord(null); setFailure(null); setCreateOpen(null); setCreating(false);
  }, [context]);
  useEffect(() => {
    if (!ready) return;
    const epoch = ++requestEpoch.current;
    const controller = new AbortController();
    setFailure(null);
    listCrmAccounts(getAccessToken(), controller.signal).then(next => {
      if (current() && !controller.signal.aborted && epoch === requestEpoch.current) setRecord({context, data: next});
    }).catch(error => {
      if (current() && !controller.signal.aborted && epoch === requestEpoch.current) {
        setFailure({context, message: userFacingViewsReportsError(error)});
      }
    });
    return () => controller.abort();
    // The render identity also fences subject and role changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context, ready, refresh, getAccessToken]);

  const handleCreate = async (input: CreateCrmAccountInput) => {
    // This is called after the modal's asynchronous field validation.
    if (!canCreate || !current() || (flight.current.context === context && flight.current.active)) return;
    const invocation = {context, active: true}; flight.current = invocation;
    setCreating(true); setFailure(null);
    try {
      const normalized = normalizeClientCreate(input);
      const signature = JSON.stringify(normalized);
      const pending = await pendingWrite("crm.account.create", session!.enterprise_id,
        user!.profile.sub, signature);
      if (!current() || !canCreate) return;
      const request_id = pending.requestId;
      ++requestEpoch.current;
      const created = await createCrmAccount(getAccessToken(), {...normalized, request_id});
      if (!current()) return;
      completePendingWrite(pending); setCreateOpen(null);
      navigate("/crm/" + created.id);
    } catch (reason) {
      if (current()) setFailure({context, message: clientCreationError(reason)});
    } finally {
      invocation.active = false;
      if (current()) setCreating(false);
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
          <Button onClick={() => void reload()} disabled={loading || creating}>刷新</Button>
          {canCreate && (
            <Button type="primary" onClick={() => setCreateOpen(context)}>新建客户档案</Button>
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
          {canCreate && (
            <Button type="primary" onClick={() => setCreateOpen(context)}>建立第一条档案</Button>
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
        key={context}
        contextKey={context}
        errorMessage={error}
        open={createOpen === context}
        onCancel={() => { if (!creating) setCreateOpen(null); }}
        onSubmit={(input) => handleCreate(input as CreateCrmAccountInput)}
      />
    </div>
  );
}
