import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
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
import LimitedMetrics from "../components/LimitedMetrics";
import P6BoundaryBanner from "../components/P6BoundaryBanner";
import QualityScenarioModal from "../components/QualityScenarioModal";
import { useP6TenantQuery } from "../hooks/useP6TenantQuery";
import {
  formatP6DateTime,
  qualityCategoryCopy,
  qualityRunStatusCopy,
  runStatusColor,
  scenarioTypeCopy,
  severityColor,
} from "../reasonCopy";
import type {
  CreateQualityScenarioInput,
  QualityRun,
  QualityScenario,
  QualitySuiteDetail,
  UpdateQualityScenarioInput,
} from "../types";
import {
  createQualityRun,
  createQualityScenario,
  getQualitySuite,
  isAutomatedQualityRequestAborted,
  updateQualityScenario,
  userFacingAutomatedQualityError,
} from "../automatedQualityApi";

const EMPTY_SUITE: QualitySuiteDetail = {
  id: "",
  enterprise_id: "",
  name: "",
  category: "qa",
  status: "active",
  created_by_user_id: "",
  created_at: "",
  updated_at: "",
  allowed_actions: [],
  scenarios: [],
  runs: [],
};

export default function QualitySuiteDetailPage() {
  const { suiteId = "" } = useParams<{ suiteId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState<QualityScenario | null>(null);
  const [inspectScenario, setInspectScenario] = useState<QualityScenario | null>(null);
  const [running, setRunning] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getQualitySuite(token, suiteId, signal),
    [suiteId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP6TenantQuery(EMPTY_SUITE, load);

  useEffect(() => {
    setScenarioOpen(false);
    setSelectedScenario(null);
    setInspectScenario(null);
    setRunning(false);
  }, [tenantEpoch]);

  const handleScenario = async (input: CreateQualityScenarioInput | UpdateQualityScenarioInput) => {
    setError(null);
    try {
      if (selectedScenario) {
        await runMutation((token, signal) =>
          updateQualityScenario(token, selectedScenario.id, input as UpdateQualityScenarioInput, signal),
        );
      } else {
        await runMutation((token, signal) =>
          createQualityScenario(token, suiteId, input as CreateQualityScenarioInput, signal),
        );
      }
      setScenarioOpen(false);
      setSelectedScenario(null);
      await reload();
    } catch (reason) {
      if (!isAutomatedQualityRequestAborted(reason)) setError(userFacingAutomatedQualityError(reason));
    }
  };

  const handleRun = async () => {
    setError(null);
    setRunning(true);
    try {
      const run = await runMutation((token, signal) => createQualityRun(token, suiteId, signal));
      navigate("/quality/runs/" + run.id);
    } catch (reason) {
      if (!isAutomatedQualityRequestAborted(reason)) setError(userFacingAutomatedQualityError(reason));
    } finally {
      setRunning(false);
    }
  };

  const scenarioColumns: TableColumnsType<QualityScenario> = [
    { title: "场景键", dataIndex: "scenario_key", width: 220, fixed: "left" },
    { title: "Oracle类型", dataIndex: "scenario_type", width: 150, render: scenarioTypeCopy },
    {
      title: "严重程度",
      dataIndex: "severity",
      width: 120,
      render: (value: string) => <Tag color={severityColor(value)}>{value}</Tag>,
    },
    {
      title: "启用",
      dataIndex: "enabled",
      width: 90,
      render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "启用" : "停用"}</Tag>,
    },
    { title: "场景SHA", dataIndex: "scenario_sha256", width: 220, ellipsis: true },
    { title: "更新时间", dataIndex: "updated_at", width: 180, render: formatP6DateTime },
    {
      title: "操作",
      key: "actions",
      width: 150,
      fixed: "right",
      render: (_, scenario) => (
        <Space size="small">
          {scenario.allowed_actions.includes("view") && <Button type="link" onClick={() => setInspectScenario(scenario)}>证据</Button>}
          {scenario.allowed_actions.includes("edit") && <Button type="link" onClick={() => { setSelectedScenario(scenario); setScenarioOpen(true); }}>编辑</Button>}
        </Space>
      ),
    },
  ];

  const renderRun = (run: QualityRun) => (
    <List.Item>
      <div style={{ width: "100%", display: "flex", gap: 12, justifyContent: "space-between", flexDirection: screens.sm ? "row" : "column" }}>
        <div>
          <Typography.Text strong>运行 {run.id.slice(-8)}</Typography.Text>
          <div><Typography.Text type="secondary">{formatP6DateTime(run.created_at)} · {run.total_count} 场景</Typography.Text></div>
        </div>
        <Space wrap>
          <Tag color={runStatusColor(run.status)}>{qualityRunStatusCopy(run.status)}</Tag>
          {run.allowed_actions.includes("view") && <Button size="small" onClick={() => navigate("/quality/runs/" + run.id)}>查看</Button>}
        </Space>
      </div>
    </List.Item>
  );

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/quality")}>← 返回质量驾驶舱</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{data.name || "质量套件"}</Typography.Title>
            {!loading && <Tag>{qualityCategoryCopy(data.category)}</Tag>}
          </Space>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("add_scenario") && <Button onClick={() => { setSelectedScenario(null); setScenarioOpen(true); }}>登记场景</Button>}
          {data.allowed_actions.includes("run") && <Button type="primary" loading={running} onClick={() => void handleRun()}>运行本地Oracle</Button>}
        </Space>
      </Space>

      <P6BoundaryBanner />

      {error && <Alert type="error" showIcon message="质量套件操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载质量套件" /></div>
      ) : !data.id && !error ? (
        <Empty description="质量套件不存在" />
      ) : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="状态">{data.status === "active" ? "活跃" : "归档"}</Descriptions.Item>
            <Descriptions.Item label="领域">{qualityCategoryCopy(data.category)}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatP6DateTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatP6DateTime(data.updated_at)}</Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p6-scenarios-heading">
            <Typography.Title id="p6-scenarios-heading" level={4}>合成场景与确定性Oracle</Typography.Title>
            {data.scenarios.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记合成场景" />
            ) : screens.md ? (
              <Table<QualityScenario> rowKey="id" dataSource={data.scenarios} columns={scenarioColumns} pagination={false} scroll={{ x: 1160 }} />
            ) : (
              <List dataSource={data.scenarios} renderItem={(scenario) => (
                <List.Item>
                  <div style={{ width: "100%" }}>
                    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{scenario.scenario_key}</Typography.Text><Tag color={severityColor(scenario.severity)}>{scenario.severity}</Tag></Space>
                    <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{scenarioTypeCopy(scenario.scenario_type)} · {scenario.enabled ? "启用" : "停用"}</Typography.Paragraph>
                    <Space wrap>
                      {scenario.allowed_actions.includes("view") && <Button onClick={() => setInspectScenario(scenario)}>查看证据</Button>}
                      {scenario.allowed_actions.includes("edit") && <Button onClick={() => { setSelectedScenario(scenario); setScenarioOpen(true); }}>编辑场景</Button>}
                    </Space>
                  </div>
                </List.Item>
              )} />
            )}
          </section>

          <section aria-labelledby="p6-suite-runs-heading">
            <Typography.Title id="p6-suite-runs-heading" level={4}>最近运行</Typography.Title>
            {data.runs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未运行该套件" /> : <List bordered dataSource={data.runs} renderItem={renderRun} />}
          </section>
        </Space>
      )}

      <QualityScenarioModal open={scenarioOpen} scenario={selectedScenario} onCancel={() => { setScenarioOpen(false); setSelectedScenario(null); }} onSubmit={handleScenario} />
      <Drawer open={Boolean(inspectScenario)} width="min(680px, 100vw)" title="有限结构化场景证据" onClose={() => setInspectScenario(null)}>
        {inspectScenario && (
          <Space direction="vertical" size={20} style={{ width: "100%" }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="场景键">{inspectScenario.scenario_key}</Descriptions.Item>
              <Descriptions.Item label="类型">{scenarioTypeCopy(inspectScenario.scenario_type)}</Descriptions.Item>
              <Descriptions.Item label="场景SHA-256"><Typography.Text code style={{ overflowWrap: "anywhere" }}>{inspectScenario.scenario_sha256}</Typography.Text></Descriptions.Item>
            </Descriptions>
            <section><Typography.Title level={5}>Oracle配置</Typography.Title><LimitedMetrics metrics={inspectScenario.oracle_config} /></section>
            <section><Typography.Title level={5}>合成观察</Typography.Title><LimitedMetrics metrics={inspectScenario.synthetic_observation} /></section>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
