import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Descriptions, Empty, Grid, List, Space, Spin, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import P7BoundaryBanner from "../components/P7BoundaryBanner";
import RehearsalCheckModal from "../components/RehearsalCheckModal";
import { useP7TenantQuery } from "../hooks/useP7TenantQuery";
import { createRehearsalCheck, createRehearsalRun, getRehearsalPlan, isLocalRehearsalRequestAborted, updateRehearsalCheck, userFacingLocalRehearsalError } from "../localRehearsalApi";
import { formatP7DateTime, rehearsalCategoryCopy, rehearsalPlanStatusCopy, rehearsalRunColor, rehearsalRunStatusCopy } from "../reasonCopy";
import type { CreateRehearsalCheckInput, RehearsalCheck, RehearsalPlanDetail, RehearsalRun, UpdateRehearsalCheckInput } from "../types";

const EMPTY_PLAN: RehearsalPlanDetail = {
  id: "", enterprise_id: "", name: "", status: "active", execution_mode: "local_manual", created_by_user_id: "", created_at: "", updated_at: "", allowed_actions: [], checks: [], recent_runs: [],
};

export default function RehearsalPlanDetailPage() {
  const { planId = "" } = useParams<{ planId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [checkOpen, setCheckOpen] = useState(false);
  const [selectedCheck, setSelectedCheck] = useState<RehearsalCheck | null>(null);
  const [starting, setStarting] = useState(false);
  const load = useCallback((token: string | null, signal: AbortSignal) => getRehearsalPlan(token, planId, signal), [planId]);
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } = useP7TenantQuery(EMPTY_PLAN, load);
  useEffect(() => { setCheckOpen(false); setSelectedCheck(null); setStarting(false); }, [tenantEpoch]);

  const saveCheck = async (input: CreateRehearsalCheckInput | UpdateRehearsalCheckInput) => {
    setError(null);
    try {
      if (selectedCheck) await runMutation((token, signal) => updateRehearsalCheck(token, selectedCheck.id, input as UpdateRehearsalCheckInput, signal));
      else await runMutation((token, signal) => createRehearsalCheck(token, planId, input as CreateRehearsalCheckInput, signal));
      setCheckOpen(false); setSelectedCheck(null); await reload();
    } catch (reason) { if (!isLocalRehearsalRequestAborted(reason)) setError(userFacingLocalRehearsalError(reason)); }
  };

  const startRun = async () => {
    setError(null); setStarting(true);
    try {
      const run = await runMutation((token, signal) => createRehearsalRun(token, planId, signal));
      navigate("/rehearsal/runs/" + run.id);
    } catch (reason) { if (!isLocalRehearsalRequestAborted(reason)) setError(userFacingLocalRehearsalError(reason)); }
    finally { setStarting(false); }
  };

  const columns: TableColumnsType<RehearsalCheck> = [
    { title: "顺序", dataIndex: "sequence_no", width: 80 },
    { title: "检查项", dataIndex: "label", width: 280, fixed: "left" },
    { title: "检查键", dataIndex: "check_key", width: 200 },
    { title: "类别", dataIndex: "category", width: 110, render: rehearsalCategoryCopy },
    { title: "要求", dataIndex: "required", width: 100, render: (value: boolean) => <Tag color={value ? "red" : "default"}>{value ? "必需" : "可选"}</Tag> },
    { title: "启用", dataIndex: "enabled", width: 90, render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "启用" : "停用"}</Tag> },
    { title: "操作", key: "actions", fixed: "right", width: 90, render: (_, check) => check.allowed_actions.includes("edit") ? <Button type="link" onClick={() => { setSelectedCheck(check); setCheckOpen(true); }}>编辑</Button> : null },
  ];

  const renderRun = (run: RehearsalRun) => <List.Item><div style={{ width: "100%", display: "flex", gap: 12, justifyContent: "space-between", flexDirection: screens.sm ? "row" : "column" }}><div><Typography.Text strong>运行 …{run.id.slice(-8)}</Typography.Text><div><Typography.Text type="secondary">{formatP7DateTime(run.created_at)} · {run.total_count} 项</Typography.Text></div></div><Space wrap>{run.rollback_required && <Tag color="red">ROLLBACK REQUIRED</Tag>}<Tag color={rehearsalRunColor(run.status)}>{rehearsalRunStatusCopy(run.status)}</Tag>{run.allowed_actions.includes("view") && <Button size="small" onClick={() => navigate("/rehearsal/runs/" + run.id)}>查看</Button>}</Space></div></List.Item>;

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div><Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/rehearsal")}>← 返回演练驾驶舱</Button><Space wrap align="center"><Typography.Title level={3} style={{ margin: 0 }}>{data.name || "演练计划"}</Typography.Title>{!loading && <Tag>{rehearsalPlanStatusCopy(data.status)}</Tag>}</Space></div>
        <Space wrap><Button onClick={() => void reload()} disabled={loading}>刷新</Button>{data.allowed_actions.includes("add_check") && <Button onClick={() => { setSelectedCheck(null); setCheckOpen(true); }}>登记检查项</Button>}{data.allowed_actions.includes("start_run") && <Button type="primary" loading={starting} onClick={() => void startRun()}>启动人工计划run</Button>}</Space>
      </Space>
      <P7BoundaryBanner />
      {error && <Alert type="error" showIcon message="演练计划操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
      {loading ? <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载演练计划" /></div> : !data.id && !error ? <Empty description="演练计划不存在" /> : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}><Descriptions.Item label="状态">{rehearsalPlanStatusCopy(data.status)}</Descriptions.Item><Descriptions.Item label="执行方式">本地人工</Descriptions.Item><Descriptions.Item label="创建时间">{formatP7DateTime(data.created_at)}</Descriptions.Item><Descriptions.Item label="更新时间">{formatP7DateTime(data.updated_at)}</Descriptions.Item></Descriptions>
          <section aria-labelledby="p7-checks-heading"><Typography.Title id="p7-checks-heading" level={4}>演练清单</Typography.Title>{data.checks.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记检查项" /> : screens.md ? <Table<RehearsalCheck> rowKey="id" dataSource={data.checks} columns={columns} pagination={false} scroll={{ x: 950 }} /> : <List dataSource={data.checks} renderItem={(check) => <List.Item><div style={{ width: "100%" }}><Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{check.sequence_no}. {check.label}</Typography.Text><Tag>{rehearsalCategoryCopy(check.category)}</Tag></Space><Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{check.required ? "必需" : "可选"} · {check.enabled ? "启用" : "停用"}</Typography.Paragraph>{check.allowed_actions.includes("edit") && <Button block onClick={() => { setSelectedCheck(check); setCheckOpen(true); }}>编辑检查项</Button>}</div></List.Item>} />}</section>
          <section aria-labelledby="p7-plan-runs-heading"><Typography.Title id="p7-plan-runs-heading" level={4}>最近run</Typography.Title>{data.recent_runs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未启动演练run" /> : <List bordered dataSource={data.recent_runs} renderItem={renderRun} />}</section>
        </Space>
      )}
      <RehearsalCheckModal open={checkOpen} check={selectedCheck} onCancel={() => { setCheckOpen(false); setSelectedCheck(null); }} onSubmit={saveCheck} />
    </div>
  );
}
