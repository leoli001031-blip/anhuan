import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Grid,
  Input,
  List,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import ScopedMaterialUploadButton from "../../p3/components/ScopedMaterialUploadButton";
import P5BoundaryBanner from "../components/P5BoundaryBanner";
import PolicySourceModal from "../components/PolicySourceModal";
import { useP5TenantQuery } from "../hooks/useP5TenantQuery";
import {
  effectColor,
  effectStatusCopy,
  policyDomainCopy,
  sourceTypeCopy,
  workflowColor,
  workflowStatusCopy,
} from "../reasonCopy";
import type {
  CreatePolicySourceInput,
  PolicyDomain,
  PolicyEffectStatus,
  PolicySearchCollection,
  PolicySearchParams,
  PolicySearchResult,
  PolicySource,
  PolicySourceCollection,
  PolicyWorkflowStatus,
} from "../types";
import {
  createPolicySource,
  isPolicyWorkflowRequestAborted,
  listPolicySources,
  searchPolicyVersions,
  userFacingPolicyWorkflowError,
} from "../policyWorkflowApi";

const EMPTY_SOURCES: PolicySourceCollection = { items: [], allowed_actions: [] };
const EMPTY_SEARCH: PolicySearchCollection = { items: [], count: 0 };

export default function PolicyLibraryPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState<PolicyDomain | undefined>();
  const [effectStatus, setEffectStatus] = useState<PolicyEffectStatus | undefined>();
  const [workflowStatus, setWorkflowStatus] = useState<PolicyWorkflowStatus | undefined>();
  const [committedSearch, setCommittedSearch] = useState<PolicySearchParams>({});
  const loadSources = useCallback(
    (token: string | null, signal: AbortSignal) => listPolicySources(token, signal),
    [],
  );
  const loadSearch = useCallback(
    (token: string | null, signal: AbortSignal) => searchPolicyVersions(token, committedSearch, signal),
    [committedSearch],
  );
  const sources = useP5TenantQuery(EMPTY_SOURCES, loadSources);
  const search = useP5TenantQuery(EMPTY_SEARCH, loadSearch);

  useEffect(() => setCreateOpen(false), [sources.tenantEpoch]);

  const handleCreate = async (input: CreatePolicySourceInput) => {
    sources.setError(null);
    try {
      const created = await sources.runMutation((token, signal) =>
        createPolicySource(token, input, signal),
      );
      setCreateOpen(false);
      navigate("/policies/sources/" + created.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) {
        sources.setError(userFacingPolicyWorkflowError(reason));
      }
    }
  };

  const sourceColumns: TableColumnsType<PolicySource> = [
    {
      title: "来源",
      dataIndex: "title",
      width: 300,
      fixed: "left",
      render: (value: string, source) =>
        source.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/policies/sources/" + source.id)}>{value}</Button>
        ) : value,
    },
    { title: "发布主体", dataIndex: "publisher", width: 220, ellipsis: true },
    { title: "类型", dataIndex: "source_type", width: 110, render: sourceTypeCopy },
    { title: "地区/层级", dataIndex: "jurisdiction", width: 150 },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: string) => <Tag color={value === "active" ? "green" : "default"}>{value === "active" ? "启用" : "归档"}</Tag>,
    },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, source) =>
        source.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/policies/sources/" + source.id)}>查看</Button>
        ) : null,
    },
  ];

  const searchColumns: TableColumnsType<PolicySearchResult> = [
    {
      title: "候选版本",
      dataIndex: "title",
      width: 280,
      fixed: "left",
      render: (value: string, version) =>
        version.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/policies/versions/" + version.id)}>{value}</Button>
        ) : value,
    },
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
    { title: "发布主体", dataIndex: "publisher", width: 200, render: (value: string | undefined) => value ?? "—" },
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
          <Typography.Title level={3} style={{ marginBottom: 4 }}>政策来源与候选版本</Typography.Title>
          <Typography.Text type="secondary">登记结构化来源、内部审核候选与分域检索</Typography.Text>
        </div>
        <Space wrap>
          {sources.data.allowed_actions.includes("create") && (
            <ScopedMaterialUploadButton
              knowledgeScope={{ kind: "service_provider", client_account_id: null }}
              defaultMaterialKind="policy"
              label="导入政策材料"
              scopeHint="此入口固定归入当前环保服务公司，并将每份材料的人工预分类默认为政策；机器建议不会改变公司归属，也不会自动写入政策库。"
            />
          )}
          <Button onClick={() => { void sources.reload(); void search.reload(); }} disabled={sources.loading || search.loading}>刷新</Button>
          {sources.data.allowed_actions.includes("create") && (
            <Button type="primary" onClick={() => setCreateOpen(true)}>登记来源</Button>
          )}
        </Space>
      </Space>

      <P5BoundaryBanner />

      {sources.error && (
        <Alert type="error" showIcon message="政策来源操作未完成" description={sources.error} action={<Button onClick={() => void sources.reload()}>重试</Button>} style={{ marginBottom: 16 }} />
      )}

      <section aria-labelledby="p5-sources-heading">
        <Typography.Title id="p5-sources-heading" level={4}>来源登记</Typography.Title>
        {sources.loading ? (
          <div style={{ minHeight: 240, display: "grid", placeItems: "center" }}><Spin tip="正在加载政策来源" /></div>
        ) : sources.data.items.length === 0 && !sources.error ? (
          <Empty description="当前企业尚无政策来源">
            {sources.data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>登记第一条来源</Button>}
          </Empty>
        ) : screens.md ? (
          <Table<PolicySource> rowKey="id" dataSource={sources.data.items} columns={sourceColumns} pagination={false} scroll={{ x: 1060 }} />
        ) : (
          <List dataSource={sources.data.items} renderItem={(source) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Typography.Text strong>{source.title}</Typography.Text>
                  <Tag>{sourceTypeCopy(source.source_type)}</Tag>
                </Space>
                <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{source.publisher} · {source.jurisdiction}</Typography.Paragraph>
                {source.allowed_actions.includes("view") && <Button block onClick={() => navigate("/policies/sources/" + source.id)}>查看来源版本</Button>}
              </div>
            </List.Item>
          )} />
        )}
      </section>

      <section aria-labelledby="p5-search-heading" style={{ marginTop: 32 }}>
        <Typography.Title id="p5-search-heading" level={4}>结构化检索</Typography.Title>
        <Typography.Paragraph type="secondary">仅检索标题、候选摘要、发布主体与内部来源引用；不检索正文或外部网络。</Typography.Paragraph>
        <Space wrap style={{ marginBottom: 16 }}>
          <Input.Search value={query} onChange={(event) => setQuery(event.target.value)} onSearch={() => setCommittedSearch({ q: query.trim() || undefined, domain, effect_status: effectStatus, workflow_status: workflowStatus })} placeholder="标题、摘要、发布主体或来源引用" style={{ width: screens.md ? 320 : "100%" }} enterButton="检索" />
          <Select allowClear value={domain} onChange={setDomain} placeholder="全部领域" style={{ width: 150 }} options={[
            { value: "safety", label: "安全" }, { value: "health", label: "职业健康" }, { value: "environment", label: "环境" }, { value: "fire", label: "消防" }, { value: "chemical", label: "化学品" }, { value: "general", label: "综合" },
          ]} />
          <Select allowClear value={effectStatus} onChange={setEffectStatus} placeholder="全部效力候选" style={{ width: 170 }} options={[
            { value: "unknown", label: "未知候选" }, { value: "not_effective", label: "尚未生效候选" }, { value: "effective", label: "有效候选" }, { value: "expired", label: "失效候选" },
          ]} />
          <Select allowClear value={workflowStatus} onChange={setWorkflowStatus} placeholder="全部工作流" style={{ width: 160 }} options={[
            { value: "draft", label: "草稿" }, { value: "in_review", label: "审核中" }, { value: "approved", label: "已通过" }, { value: "rejected", label: "已退回" }, { value: "published", label: "内部发布" }, { value: "superseded", label: "已被替代" },
          ]} />
          <Button onClick={() => setCommittedSearch({ q: query.trim() || undefined, domain, effect_status: effectStatus, workflow_status: workflowStatus })}>应用筛选</Button>
        </Space>

        {search.error && <Alert type="error" showIcon message="结构化检索失败" description={search.error} action={<Button onClick={() => void search.reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
        {search.loading ? (
          <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}><Spin tip="正在检索结构化元数据" /></div>
        ) : search.data.items.length === 0 && !search.error ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的候选版本" />
        ) : screens.md ? (
          <Table<PolicySearchResult> rowKey="id" dataSource={search.data.items} columns={searchColumns} pagination={false} scroll={{ x: 960 }} />
        ) : (
          <List dataSource={search.data.items} renderItem={(version) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Typography.Text strong>{version.title}</Typography.Text>
                <div style={{ margin: "8px 0" }}><Space wrap><Tag>{policyDomainCopy(version.domain)}</Tag><Tag color={effectColor(version.effect_status)}>{effectStatusCopy(version.effect_status)}</Tag><Tag color={workflowColor(version.workflow_status)}>{workflowStatusCopy(version.workflow_status)}</Tag></Space></div>
                {version.summary && <Typography.Paragraph ellipsis={{ rows: 2 }}>{version.summary}</Typography.Paragraph>}
                {version.allowed_actions.includes("view") && <Button block onClick={() => navigate("/policies/versions/" + version.id)}>查看候选</Button>}
              </div>
            </List.Item>
          )} />
        )}
      </section>

      <PolicySourceModal open={createOpen} onCancel={() => setCreateOpen(false)} onSubmit={(input) => handleCreate(input as CreatePolicySourceInput)} />
    </div>
  );
}
