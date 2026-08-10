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
import P5BoundaryBanner from "../components/P5BoundaryBanner";
import PolicySourceModal from "../components/PolicySourceModal";
import PolicyVersionModal from "../components/PolicyVersionModal";
import { useP5TenantQuery } from "../hooks/useP5TenantQuery";
import {
  effectColor,
  effectStatusCopy,
  formatP5Date,
  formatP5DateTime,
  policyDomainCopy,
  sourceTypeCopy,
  workflowColor,
  workflowStatusCopy,
} from "../reasonCopy";
import type {
  CreatePolicyVersionInput,
  PolicySourceDetail,
  PolicyVersion,
  UpdatePolicySourceInput,
} from "../types";
import {
  createPolicyVersion,
  getPolicySource,
  isPolicyWorkflowRequestAborted,
  updatePolicySource,
  userFacingPolicyWorkflowError,
} from "../policyWorkflowApi";

const EMPTY_SOURCE: PolicySourceDetail = {
  id: "",
  enterprise_id: "",
  title: "",
  publisher: "",
  source_type: "internal",
  jurisdiction: "",
  source_reference: "",
  status: "active",
  created_by_user_id: "",
  created_at: "",
  updated_at: "",
  allowed_actions: [],
  versions: [],
};

export default function PolicySourceDetailPage() {
  const { sourceId = "" } = useParams<{ sourceId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [editOpen, setEditOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getPolicySource(token, sourceId, signal),
    [sourceId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP5TenantQuery(EMPTY_SOURCE, load);

  useEffect(() => {
    setEditOpen(false);
    setVersionOpen(false);
  }, [tenantEpoch]);

  const handleUpdate = async (input: UpdatePolicySourceInput) => {
    setError(null);
    try {
      await runMutation((token, signal) => updatePolicySource(token, sourceId, input, signal));
      setEditOpen(false);
      await reload();
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const handleCreateVersion = async (input: CreatePolicyVersionInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createPolicyVersion(token, sourceId, input, signal),
      );
      setVersionOpen(false);
      navigate("/policies/versions/" + created.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const versionColumns: TableColumnsType<PolicyVersion> = [
    {
      title: "版本候选",
      dataIndex: "version_number",
      width: 120,
      fixed: "left",
      render: (value: number, version) =>
        version.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/policies/versions/" + version.id)}>v{value}</Button>
        ) : `v${value}`,
    },
    { title: "标题", dataIndex: "title", width: 280, ellipsis: true },
    { title: "领域", dataIndex: "domain", width: 110, render: policyDomainCopy },
    {
      title: "效力候选",
      dataIndex: "effect_status",
      width: 150,
      render: (value: string) => <Tag color={effectColor(value)}>{effectStatusCopy(value)}</Tag>,
    },
    {
      title: "工作流",
      dataIndex: "workflow_status",
      width: 130,
      render: (value: string) => <Tag color={workflowColor(value)}>{workflowStatusCopy(value)}</Tag>,
    },
    { title: "生效起日", dataIndex: "effective_from", width: 130, render: formatP5Date },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, version) =>
        version.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/policies/versions/" + version.id)}>查看</Button>
        ) : null,
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/policies")}>← 返回政策来源</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{data.title || "政策来源"}</Typography.Title>
            {!loading && <Tag color={data.status === "active" ? "green" : "default"}>{data.status === "active" ? "启用" : "归档"}</Tag>}
          </Space>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("edit") && <Button onClick={() => setEditOpen(true)}>编辑来源</Button>}
          {data.allowed_actions.includes("create_version") && <Button type="primary" onClick={() => setVersionOpen(true)}>建立版本候选</Button>}
        </Space>
      </Space>

      <P5BoundaryBanner />

      {error && <Alert type="error" showIcon message="政策来源操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载政策来源" /></div>
      ) : !data.id && !error ? (
        <Empty description="政策来源不存在" />
      ) : (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="发布主体">{data.publisher}</Descriptions.Item>
            <Descriptions.Item label="来源类型">{sourceTypeCopy(data.source_type)}</Descriptions.Item>
            <Descriptions.Item label="地区/层级">{data.jurisdiction}</Descriptions.Item>
            <Descriptions.Item label="内部来源引用"><Typography.Text code style={{ overflowWrap: "anywhere" }}>{data.source_reference}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatP5DateTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatP5DateTime(data.updated_at)}</Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p5-source-versions-heading">
            <Typography.Title id="p5-source-versions-heading" level={4}>版本候选</Typography.Title>
            {data.versions.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未建立版本候选" />
            ) : screens.md ? (
              <Table<PolicyVersion> rowKey="id" dataSource={data.versions} columns={versionColumns} pagination={false} scroll={{ x: 1120 }} />
            ) : (
              <List dataSource={data.versions} renderItem={(version) => (
                <List.Item>
                  <div style={{ width: "100%" }}>
                    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                      <Typography.Text strong>v{version.version_number} · {version.title}</Typography.Text>
                      <Tag color={workflowColor(version.workflow_status)}>{workflowStatusCopy(version.workflow_status)}</Tag>
                    </Space>
                    <div style={{ margin: "8px 0" }}><Space wrap><Tag>{policyDomainCopy(version.domain)}</Tag><Tag color={effectColor(version.effect_status)}>{effectStatusCopy(version.effect_status)}</Tag></Space></div>
                    {version.allowed_actions.includes("view") && <Button block onClick={() => navigate("/policies/versions/" + version.id)}>查看候选</Button>}
                  </div>
                </List.Item>
              )} />
            )}
          </section>
        </Space>
      )}

      <PolicySourceModal open={editOpen} source={data} onCancel={() => setEditOpen(false)} onSubmit={(input) => handleUpdate(input as UpdatePolicySourceInput)} />
      <PolicyVersionModal open={versionOpen} onCancel={() => setVersionOpen(false)} onSubmit={handleCreateVersion} />
    </div>
  );
}
