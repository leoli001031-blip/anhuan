import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Grid,
  List,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import P6BoundaryBanner from "../components/P6BoundaryBanner";
import QualitySuiteModal from "../components/QualitySuiteModal";
import { useP6TenantQuery } from "../hooks/useP6TenantQuery";
import {
  formatP6DateTime,
  qualityCategoryCopy,
  qualityRunStatusCopy,
  runStatusColor,
} from "../reasonCopy";
import type {
  CreateQualitySuiteInput,
  QualityDashboard,
  QualityRun,
  QualitySuite,
  QualitySuiteCollection,
} from "../types";
import {
  createQualitySuite,
  getQualityDashboard,
  isAutomatedQualityRequestAborted,
  listQualitySuites,
  userFacingAutomatedQualityError,
} from "../automatedQualityApi";

const EMPTY_DASHBOARD: QualityDashboard = {
  synthetic_label: "合成场景",
  suite_counts: { total: 0, active: 0, archived: 0 },
  scenario_counts: { total: 0, enabled: 0, disabled: 0 },
  run_counts: { total: 0, queued: 0, running: 0, passed: 0, failed: 0, cancelled: 0 },
  result_counts: { total: 0, passed: 0, failed: 0, error: 0 },
  disagreement_counts: { total: 0, open: 0, acknowledged: 0, waived: 0 },
  recent_runs: [],
  allowed_actions: [],
  boundaries: [],
};
const EMPTY_SUITES: QualitySuiteCollection = { items: [], allowed_actions: [] };

export default function QualityDashboardPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const loadDashboard = useCallback(
    (token: string | null, signal: AbortSignal) => getQualityDashboard(token, signal),
    [],
  );
  const loadSuites = useCallback(
    (token: string | null, signal: AbortSignal) => listQualitySuites(token, signal),
    [],
  );
  const dashboard = useP6TenantQuery(EMPTY_DASHBOARD, loadDashboard);
  const suites = useP6TenantQuery(EMPTY_SUITES, loadSuites);
  const metricCards = [
    ["活跃套件", dashboard.data.suite_counts.active],
    ["启用场景", dashboard.data.scenario_counts.enabled],
    ["合成运行", dashboard.data.run_counts.total],
    ["通过运行", dashboard.data.run_counts.passed],
    ["失败运行", dashboard.data.run_counts.failed],
    ["通过结果", dashboard.data.result_counts.passed],
    ["失败/错误结果", dashboard.data.result_counts.failed + dashboard.data.result_counts.error],
    ["待处置分歧", dashboard.data.disagreement_counts.open],
  ] as const;

  useEffect(() => setCreateOpen(false), [suites.tenantEpoch]);

  const handleCreate = async (input: CreateQualitySuiteInput) => {
    suites.setError(null);
    try {
      const created = await suites.runMutation((token, signal) =>
        createQualitySuite(token, input, signal),
      );
      setCreateOpen(false);
      navigate("/quality/suites/" + created.id);
    } catch (reason) {
      if (!isAutomatedQualityRequestAborted(reason)) {
        suites.setError(userFacingAutomatedQualityError(reason));
      }
    }
  };

  const suiteColumns: TableColumnsType<QualitySuite> = [
    {
      title: "质量套件",
      dataIndex: "name",
      width: 300,
      fixed: "left",
      render: (value: string, suite) =>
        suite.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/quality/suites/" + suite.id)}>{value}</Button>
        ) : value,
    },
    { title: "领域", dataIndex: "category", width: 130, render: qualityCategoryCopy },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: string) => <Tag color={value === "active" ? "green" : "default"}>{value === "active" ? "活跃" : "归档"}</Tag>,
    },
    { title: "场景数", dataIndex: "scenario_count", width: 100, render: (value: number | undefined) => value ?? "—" },
    { title: "运行数", dataIndex: "run_count", width: 100, render: (value: number | undefined) => value ?? "—" },
    { title: "更新时间", dataIndex: "updated_at", width: 180, render: formatP6DateTime },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, suite) =>
        suite.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/quality/suites/" + suite.id)}>查看</Button>
        ) : null,
    },
  ];

  const renderRun = (run: QualityRun) => (
    <List.Item>
      <div style={{ width: "100%", display: "flex", gap: 12, alignItems: screens.sm ? "center" : "flex-start", justifyContent: "space-between", flexDirection: screens.sm ? "row" : "column" }}>
        <div>
          <Typography.Text strong>{run.suite_name ?? `套件 ${run.suite_id.slice(-8)}`}</Typography.Text>
          <div><Typography.Text type="secondary">{formatP6DateTime(run.created_at)} · {run.total_count} 个合成场景</Typography.Text></div>
        </div>
        <Space wrap>
          <Tag color={runStatusColor(run.status)}>{qualityRunStatusCopy(run.status)}</Tag>
          {run.allowed_actions.includes("view") && <Button size="small" onClick={() => navigate("/quality/runs/" + run.id)}>查看结果</Button>}
        </Space>
      </div>
    </List.Item>
  );

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>合成质量驾驶舱</Typography.Title>
          <Typography.Text type="secondary">本地确定性 Oracle、不可变结果和人工分歧处置</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => { void dashboard.reload(); void suites.reload(); }} disabled={dashboard.loading || suites.loading}>刷新</Button>
          <Button onClick={() => navigate("/quality/disagreements")}>查看分歧</Button>
          {suites.data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>创建套件</Button>}
        </Space>
      </Space>

      <P6BoundaryBanner />

      {(dashboard.error || suites.error) && (
        <Alert type="error" showIcon message="质量驾驶舱加载失败" description={dashboard.error ?? suites.error} action={<Button onClick={() => { void dashboard.reload(); void suites.reload(); }}>重试</Button>} style={{ marginBottom: 16 }} />
      )}

      {dashboard.loading ? (
        <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}><Spin tip="正在汇总合成质量指标" /></div>
      ) : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <section aria-labelledby="p6-metrics-heading">
            <Space align="baseline"><Typography.Title id="p6-metrics-heading" level={4}>合成场景指标</Typography.Title><Typography.Text type="secondary">{dashboard.data.synthetic_label}</Typography.Text></Space>
            <div style={{ display: "grid", gridTemplateColumns: screens.md ? "repeat(4, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))", borderTop: "1px solid #f0f0f0", borderLeft: "1px solid #f0f0f0" }}>
              {metricCards.map(([label, value]) => (
                <div key={label} style={{ padding: screens.md ? 20 : 14, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}>
                  <Statistic title={label} value={value} />
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="p6-recent-runs-heading">
            <Typography.Title id="p6-recent-runs-heading" level={4}>最近运行</Typography.Title>
            {dashboard.data.recent_runs.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无合成运行" />
            ) : (
              <List bordered dataSource={dashboard.data.recent_runs} renderItem={renderRun} />
            )}
          </section>
        </Space>
      )}

      <section aria-labelledby="p6-suites-heading" style={{ marginTop: 32 }}>
        <Typography.Title id="p6-suites-heading" level={4}>质量套件</Typography.Title>
        {suites.loading ? (
          <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}><Spin tip="正在加载质量套件" /></div>
        ) : suites.data.items.length === 0 && !suites.error ? (
          <Empty description="当前企业尚无质量套件">
            {suites.data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>创建第一套合成检查</Button>}
          </Empty>
        ) : screens.md ? (
          <Table<QualitySuite> rowKey="id" dataSource={suites.data.items} columns={suiteColumns} pagination={false} scroll={{ x: 1000 }} />
        ) : (
          <List dataSource={suites.data.items} renderItem={(suite) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{suite.name}</Typography.Text><Tag>{qualityCategoryCopy(suite.category)}</Tag></Space>
                <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{suite.status === "active" ? "活跃" : "归档"}</Typography.Paragraph>
                {suite.allowed_actions.includes("view") && <Button block onClick={() => navigate("/quality/suites/" + suite.id)}>查看场景</Button>}
              </div>
            </List.Item>
          )} />
        )}
      </section>

      <QualitySuiteModal open={createOpen} onCancel={() => setCreateOpen(false)} onSubmit={handleCreate} />
    </div>
  );
}
