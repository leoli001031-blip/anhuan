import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
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
import { useNavigate, useParams } from "react-router-dom";
import ScopedMaterialUploadButton from "../../p3/components/ScopedMaterialUploadButton";
import CrmAccountModal from "../components/CrmAccountModal";
import CrmContactDrawer from "../components/CrmContactDrawer";
import CrmFollowUpModal from "../components/CrmFollowUpModal";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import {
  crmStageCopy,
  followUpChannelCopy,
  formatP4DateTime,
  stageColor,
} from "../reasonCopy";
import type {
  CreateCrmContactInput,
  CreateCrmFollowUpInput,
  CrmAccountDetail,
  CrmContact,
  UpdateCrmAccountInput,
  UpdateCrmContactInput,
} from "../types";
import {
  createCrmContact,
  createCrmFollowUp,
  getCrmAccount,
  isViewsReportsRequestAborted,
  updateCrmAccount,
  updateCrmContact,
  userFacingViewsReportsError,
} from "../viewsReportsApi";

const EMPTY_ACCOUNT: CrmAccountDetail = {
  id: "",
  display_name: "",
  stage: "lead",
  owner_user_id: null,
  industry_note: null,
  region_note: null,
  next_follow_up_at: null,
  created_at: "",
  updated_at: "",
  allowed_actions: [],
  contacts: [],
  follow_ups: [],
};

export default function CrmAccountDetailPage() {
  const { accountId = "" } = useParams<{ accountId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [editOpen, setEditOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [selectedContact, setSelectedContact] = useState<CrmContact | null>(null);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getCrmAccount(token, accountId, signal),
    [accountId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP4TenantQuery(EMPTY_ACCOUNT, load);

  useEffect(() => {
    setEditOpen(false);
    setContactOpen(false);
    setFollowUpOpen(false);
    setSelectedContact(null);
  }, [tenantEpoch]);

  const handleAccountUpdate = async (input: UpdateCrmAccountInput) => {
    setError(null);
    try {
      await runMutation((token, signal) => updateCrmAccount(token, accountId, input, signal));
      setEditOpen(false);
      await reload();
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const handleContactSubmit = async (input: CreateCrmContactInput | UpdateCrmContactInput) => {
    setError(null);
    try {
      if (selectedContact) {
        await runMutation((token, signal) =>
          updateCrmContact(token, selectedContact.id, input, signal),
        );
      } else {
        await runMutation((token, signal) =>
          createCrmContact(token, accountId, input as CreateCrmContactInput, signal),
        );
      }
      setContactOpen(false);
      setSelectedContact(null);
      await reload();
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const handleFollowUp = async (input: CreateCrmFollowUpInput) => {
    setError(null);
    try {
      await runMutation((token, signal) => createCrmFollowUp(token, accountId, input, signal));
      setFollowUpOpen(false);
      await reload();
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const contactColumns: TableColumnsType<CrmContact> = [
    { title: "姓名", dataIndex: "display_name", width: 150 },
    { title: "职务", dataIndex: "role_title", width: 160, render: (value: string | null) => value ?? "—" },
    { title: "邮箱", dataIndex: "email", width: 220, ellipsis: true, render: (value: string | null) => value ?? "—" },
    { title: "电话", dataIndex: "phone", width: 160, render: (value: string | null) => value ?? "—" },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (value: string) => <Tag color={value === "active" ? "green" : "default"}>{value === "active" ? "有效" : "停用"}</Tag>,
    },
    {
      title: "操作",
      key: "actions",
      width: 90,
      render: (_, contact) =>
        contact.allowed_actions.includes("edit") ? (
          <Button type="link" onClick={() => { setSelectedContact(contact); setContactOpen(true); }}>编辑</Button>
        ) : null,
    },
  ];
  const canOpenClientMaterialUpload =
    Boolean(data.id) &&
    data.allowed_actions.some((action) =>
      ["view", "edit", "add_contact", "add_follow_up"].includes(action),
    );

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/crm")}>← 返回客户档案</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{data.display_name || "客户档案"}</Typography.Title>
            {!loading && <Tag color={stageColor(data.stage)}>{crmStageCopy(data.stage)}</Tag>}
          </Space>
        </div>
        <Space wrap>
          {canOpenClientMaterialUpload && (
            <ScopedMaterialUploadButton
              knowledgeScope={{
                kind: "client",
                client_account_id: data.id,
                client_display_name: data.display_name,
              }}
              defaultMaterialKind="unknown"
              label="上传客户材料"
              scopeHint={`此入口固定归入客户“${data.display_name}”；机器只能建议材料类型，不能更改客户归属。`}
            />
          )}
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("edit") && <Button onClick={() => setEditOpen(true)}>编辑档案</Button>}
          {data.allowed_actions.includes("add_contact") && (
            <Button onClick={() => { setSelectedContact(null); setContactOpen(true); }}>新增联系人</Button>
          )}
          {data.allowed_actions.includes("add_follow_up") && (
            <Button type="primary" onClick={() => setFollowUpOpen(true)}>登记跟进</Button>
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
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载客户档案" /></div>
      ) : !data.id && !error ? (
        <Empty description="客户档案不存在" />
      ) : (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="负责人 ID">{data.owner_user_id ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="下次跟进">{formatP4DateTime(data.next_follow_up_at)}</Descriptions.Item>
            <Descriptions.Item label="行业备注">{data.industry_note ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="区域备注">{data.region_note ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatP4DateTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatP4DateTime(data.updated_at)}</Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p4-contacts-heading">
            <Typography.Title id="p4-contacts-heading" level={4}>联系人</Typography.Title>
            {data.contacts.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记联系人" />
            ) : screens.md ? (
              <Table<CrmContact> rowKey="id" dataSource={data.contacts} columns={contactColumns} pagination={false} scroll={{ x: 1050 }} />
            ) : (
              <List
                dataSource={data.contacts}
                renderItem={(contact) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Typography.Text strong>{contact.display_name}</Typography.Text>
                        <Tag color={contact.status === "active" ? "green" : "default"}>{contact.status === "active" ? "有效" : "停用"}</Tag>
                      </Space>
                      <Typography.Paragraph style={{ margin: "8px 0 0" }}>{contact.role_title ?? "未登记职务"}</Typography.Paragraph>
                      <Typography.Text type="secondary">{contact.email ?? "—"} · {contact.phone ?? "—"}</Typography.Text>
                      {contact.allowed_actions.includes("edit") && (
                        <Button block style={{ marginTop: 12 }} onClick={() => { setSelectedContact(contact); setContactOpen(true); }}>编辑联系人</Button>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </section>

          <section aria-labelledby="p4-follow-ups-heading">
            <Typography.Title id="p4-follow-ups-heading" level={4}>人工跟进</Typography.Title>
            {data.follow_ups.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记跟进" />
            ) : (
              <List
                bordered
                dataSource={data.follow_ups}
                renderItem={(followUp) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Tag>{followUpChannelCopy(followUp.channel)}</Tag>
                        <Typography.Text type="secondary">{formatP4DateTime(followUp.occurred_at)}</Typography.Text>
                      </Space>
                      <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: "10px 0" }}>{followUp.summary}</Typography.Paragraph>
                      {followUp.next_action && <Typography.Text>下一步：{followUp.next_action}</Typography.Text>}
                      {followUp.next_due_at && <div><Typography.Text type="secondary">到期：{formatP4DateTime(followUp.next_due_at)}</Typography.Text></div>}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </section>
        </Space>
      )}

      <CrmAccountModal
        open={editOpen}
        account={data}
        onCancel={() => setEditOpen(false)}
        onSubmit={(input) => handleAccountUpdate(input as UpdateCrmAccountInput)}
      />
      <CrmContactDrawer
        open={contactOpen}
        contact={selectedContact}
        onCancel={() => { setContactOpen(false); setSelectedContact(null); }}
        onSubmit={handleContactSubmit}
      />
      <CrmFollowUpModal open={followUpOpen} onCancel={() => setFollowUpOpen(false)} onSubmit={handleFollowUp} />
    </div>
  );
}
