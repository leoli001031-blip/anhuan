import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Grid, List, Space, Spin, Statistic, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import P7BoundaryBanner from "../components/P7BoundaryBanner";
import RehearsalPlanModal from "../components/RehearsalPlanModal";
import { useP7TenantQuery } from "../hooks/useP7TenantQuery";
import {
  createRehearsalPlan,
  getLocalRehearsalDashboard,
  isLocalRehearsalRequestAborted,
  listRehearsalPlans,
  userFacingLocalRehearsalError,
} from "../localRehearsalApi";
import { formatP7DateTime, rehearsalPlanStatusCopy, rehearsalRunColor, rehearsalRunStatusCopy } from "../reasonCopy";
import type { CreateRehearsalPlanInput, LocalRehearsalDashboard, RehearsalPlan, RehearsalPlanCollection, RehearsalRun } from "../types";

const EMPTY_DASHBOARD: LocalRehearsalDashboard = {
  rehearsal_label: "本地人工演练",
  plan_counts: { total: 0, draft: 0, active: 0, archived: 0 },
  run_counts: { total: 0, planned: 0, running: 0, passed: 0, failed: 0, cancelled: 0 },
  result_counts: { total: 0, pending: 0, passed: 0, failed: 0, blocked: 0 },
  rollback_required_count: 0,
  pending_plans: [],
  recent_runs: [],
  allowed_actions: [],
  boundaries: [],
};
const EMPTY_PLANS: RehearsalPlanCollection = { items: [], allowed_actions: [] };

export default function LocalRehearsalDashboardPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const loadDashboard = useCallback((token: string | null, signal: AbortSignal) => getLocalRehearsalDashboard(token, signal), []);
  const loadPlans = useCallback((token: string | null, signal: AbortSignal) => listRehearsalPlans(token, signal), []);
  const dashboard = useP7TenantQuery(EMPTY_DASHBOARD, loadDashboard);
  const plans = useP7TenantQuery(EMPTY_PLANS, loadPlans);
  useEffect(() => setCreateOpen(false), [plans.tenantEpoch]);

  const createPlan = async (input: CreateRehearsalPlanInput) => {
    plans.setError(null);
    try {
      const created = await plans.runMutation((token, signal) => createRehearsalPlan(token, input, signal));
      setCreateOpen(false);
      navigate("/rehearsal/plans/" + created.id);
    } catch (reason) {
      if (!isLocalRehearsalRequestAborted(reason)) plans.setError(userFacingLocalRehearsalError(reason));
    }
  };

  const metricRows = [
    ["可演练计划", dashboard.data.plan_counts.active],
    ["进行中run", dashboard.data.run_counts.running],
    ["待记录检查", dashboard.data.result_counts.pending],
    ["已通过run", dashboard.data.run_counts.passed],
    ["未通过run", dashboard.data.run_counts.failed],
    ["失败检查", dashboard.data.result_counts.failed],
    ["阻断检查", dashboard.data.result_counts.blocked],
    ["需要回滚", dashboard.data.rollback_required_count],
  ] as const;

  const planColumns: TableColumnsType<RehearsalPlan> = [
    { title: "计划", dataIndex: "name", width: 300, fixed: "left", render: (value: string, plan) => plan.allowed_actions.includes("view") ? <Button type="link" onClick={() => navigate("/rehearsal/plans/" + plan.id)}>{value}</Button> : value },
    { title: "状态", dataIndex: "status", width: 110, render: (value: string) => <Tag color={value === "active" ? "green" : "default"}>{rehearsalPlanStatusCopy(value)}</Tag> },
    { title: "执行方式", dataIndex: "execution_mode", width: 150, render: () => "本地人工" },
    { title: "更新时间", dataIndex: "updated_at", width: 180, render: formatP7DateTime },
    { title: "操作", key: "actions", fixed: "right", width: 100, render: (_, plan) => plan.allowed_actions.includes("view") ? <Button type="link" onClick={() => navigate("/rehearsal/plans/" + plan.id)}>查看</Button> : null },
  ];

  const renderRun = (run: RehearsalRun) => (
    <List.Item>
      <div style={{ width: "100%", display: "flex", gap: 12, justifyContent: "space-between", alignItems: screens.sm ? "center" : "flex-start", flexDirection: screens.sm ? "row" : "column" }}>
        <div><Typography.Text strong>运行 …{run.id.slice(-8)}</Typography.Text><div><Typography.Text type="secondary">{formatP7DateTime(run.created_at)} · {run.total_count} 项人工计划检查</Typography.Text></div></div>
        <Space wrap>
          {run.rollback_required && <Tag color="red">ROLLBACK REQUIRED</Tag>}
          <Tag color={rehearsalRunColor(run.status)}>{rehearsalRunStatusCopy(run.status)}</Tag>
          {run.allowed_actions.includes("view") && <Button size="small" onClick={() => navigate("/rehearsal/runs/" + run.id)}>查看run</Button>}
        </Space>
      </div>
    </List.Item>
  );

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div><Typography.Title level={3} style={{ marginBottom: 4 }}>本地演练驾驶舱</Typography.Title><Typography.Text type="secondary">人工计划、冻结清单、不可变结果与回滚门</Typography.Text></div>
        <Space wrap>
          <Button onClick={() => { void dashboard.reload(); void plans.reload(); }} disabled={dashboard.loading || plans.loading}>刷新</Button>
          {plans.data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>创建演练计划</Button>}
        </Space>
      </Space>
      <P7BoundaryBanner />
      {(dashboard.error || plans.error) && <Alert type="error" showIcon message="本地演练驾驶舱加载失败" description={dashboard.error ?? plans.error} action={<Button onClick={() => { void dashboard.reload(); void plans.reload(); }}>重试</Button>} style={{ marginBottom: 16 }} />}

      {dashboard.loading ? <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}><Spin tip="正在汇总本地人工演练" /></div> : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <section aria-labelledby="p7-metrics-heading">
            <Space align="baseline"><Typography.Title id="p7-metrics-heading" level={4}>演练状态</Typography.Title><Typography.Text type="secondary">{dashboard.data.rehearsal_label}</Typography.Text></Space>
            <div style={{ display: "grid", gridTemplateColumns: screens.md ? "repeat(4, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))", borderTop: "1px solid #f0f0f0", borderLeft: "1px solid #f0f0f0" }}>
              {metricRows.map(([label, value]) => <div key={label} style={{ padding: screens.md ? 20 : 14, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}><Statistic title={label} value={value} valueStyle={label === "需要回滚" && value > 0 ? { color: "#cf1322" } : undefined} /></div>)}
            </div>
          </section>
          <section aria-labelledby="p7-runs-heading"><Typography.Title id="p7-runs-heading" level={4}>最近run</Typography.Title>{dashboard.data.recent_runs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无本地演练run" /> : <List bordered dataSource={dashboard.data.recent_runs} renderItem={renderRun} />}</section>
        </Space>
      )}

      <section aria-labelledby="p7-plans-heading" style={{ marginTop: 32 }}>
        <Typography.Title id="p7-plans-heading" level={4}>演练计划</Typography.Title>
        {plans.loading ? <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}><Spin tip="正在加载演练计划" /></div> : plans.data.items.length === 0 && !plans.error ? (
          <Empty description="当前企业尚无演练计划">{plans.data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>创建第一个演练计划</Button>}</Empty>
        ) : screens.md ? <Table<RehearsalPlan> rowKey="id" dataSource={plans.data.items} columns={planColumns} pagination={false} scroll={{ x: 880 }} /> : (
          <List dataSource={plans.data.items} renderItem={(plan) => <List.Item><div style={{ width: "100%" }}><Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{plan.name}</Typography.Text><Tag>{rehearsalPlanStatusCopy(plan.status)}</Tag></Space><Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>本地人工 · {formatP7DateTime(plan.updated_at)}</Typography.Paragraph>{plan.allowed_actions.includes("view") && <Button block onClick={() => navigate("/rehearsal/plans/" + plan.id)}>查看清单</Button>}</div></List.Item>} />
        )}
      </section>
      <RehearsalPlanModal open={createOpen} onCancel={() => setCreateOpen(false)} onSubmit={createPlan} />
    </div>
  );
}
