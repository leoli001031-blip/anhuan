import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Descriptions, Drawer, Empty, Grid, List, Space, Spin, Statistic, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import P7BoundaryBanner from "../components/P7BoundaryBanner";
import RehearsalResultModal from "../components/RehearsalResultModal";
import RehearsalRunActionModal from "../components/RehearsalRunActionModal";
import { useP7TenantQuery } from "../hooks/useP7TenantQuery";
import { cancelRehearsalRun, completeRehearsalRun, getRehearsalRun, isLocalRehearsalRequestAborted, recordRehearsalResult, userFacingLocalRehearsalError } from "../localRehearsalApi";
import { formatP7DateTime, p7ReasonCopy, rehearsalCategoryCopy, rehearsalResultColor, rehearsalResultStatusCopy, rehearsalRunColor, rehearsalRunStatusCopy } from "../reasonCopy";
import type { RecordRehearsalResultInput, RehearsalCheckResult, RehearsalRunDetail } from "../types";

const EMPTY_RUN: RehearsalRunDetail = {
  id: "", enterprise_id: "", plan_id: "", status: "planned", total_count: 0, pending_count: 0, passed_count: 0, failed_count: 0, blocked_count: 0, rollback_required: false, created_by_user_id: "", created_at: "", started_at: null, completed_at: null, allowed_actions: [], results: [],
};

export default function RehearsalRunDetailPage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [recordTarget, setRecordTarget] = useState<RehearsalCheckResult | null>(null);
  const [inspectTarget, setInspectTarget] = useState<RehearsalCheckResult | null>(null);
  const [runAction, setRunAction] = useState<"complete" | "cancel" | null>(null);
  const load = useCallback((token: string | null, signal: AbortSignal) => getRehearsalRun(token, runId, signal), [runId]);
  const { data, setData, loading, error, setError, reload, runMutation, tenantEpoch } = useP7TenantQuery(EMPTY_RUN, load);
  useEffect(() => { setRecordTarget(null); setInspectTarget(null); setRunAction(null); }, [tenantEpoch]);

  const rollbackGate = data.rollback_required || data.results.some((result) => result.required && (result.status === "failed" || result.status === "blocked"));

  const recordResult = async (input: RecordRehearsalResultInput) => {
    if (!recordTarget) return;
    setError(null);
    try {
      const updated = await runMutation((token, signal) => recordRehearsalResult(token, runId, recordTarget.id, input, signal));
      setData(updated); setRecordTarget(null);
    } catch (reason) { if (!isLocalRehearsalRequestAborted(reason)) setError(userFacingLocalRehearsalError(reason)); }
  };

  const closeRun = async () => {
    if (!runAction) return;
    setError(null);
    try {
      const updated = await runMutation((token, signal) => runAction === "complete" ? completeRehearsalRun(token, runId, signal) : cancelRehearsalRun(token, runId, signal));
      setData(updated); setRunAction(null);
    } catch (reason) { if (!isLocalRehearsalRequestAborted(reason)) setError(userFacingLocalRehearsalError(reason)); }
  };

  const columns: TableColumnsType<RehearsalCheckResult> = [
    { title: "顺序", dataIndex: "sequence_no", width: 80 },
    { title: "冻结检查项", dataIndex: "label", width: 280, fixed: "left" },
    { title: "类别", dataIndex: "category", width: 110, render: rehearsalCategoryCopy },
    { title: "要求", dataIndex: "required", width: 90, render: (value: boolean) => <Tag color={value ? "red" : "default"}>{value ? "必需" : "可选"}</Tag> },
    { title: "结果", dataIndex: "status", width: 110, render: (value: string) => <Tag color={rehearsalResultColor(value)}>{rehearsalResultStatusCopy(value)}</Tag> },
    { title: "原因", dataIndex: "reason_code", width: 220, render: p7ReasonCopy },
    { title: "记录时间", dataIndex: "recorded_at", width: 180, render: formatP7DateTime },
    { title: "操作", key: "actions", fixed: "right", width: 170, render: (_, result) => <Space size="small">{result.allowed_actions.includes("view") && <Button type="link" onClick={() => setInspectTarget(result)}>证据</Button>}{result.allowed_actions.includes("record") && <Button type="link" onClick={() => setRecordTarget(result)}>记录</Button>}</Space> },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div><Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/rehearsal/plans/" + data.plan_id)}>← 返回演练计划</Button><Space wrap align="center"><Typography.Title level={3} style={{ margin: 0 }}>本地演练run …{runId.slice(-8)}</Typography.Title>{!loading && <Tag color={rehearsalRunColor(data.status)}>{rehearsalRunStatusCopy(data.status)}</Tag>}</Space></div>
        <Space wrap><Button onClick={() => void reload()} disabled={loading}>刷新</Button>{data.allowed_actions.includes("complete") && <Button type="primary" onClick={() => setRunAction("complete")}>完成run</Button>}{data.allowed_actions.includes("cancel") && <Button danger onClick={() => setRunAction("cancel")}>取消run</Button>}</Space>
      </Space>
      <P7BoundaryBanner />
      {rollbackGate ? <Alert type="error" showIcon message="ROLLBACK REQUIRED" description="该run存在失败或阻断的必需检查。这里只记录回滚门，不会执行回滚或恢复动作。" style={{ marginBottom: 16 }} /> : <Alert type="info" showIcon message="当前未触发回滚门" description="只有人工计划记录全部收口后，后端完成门才会决定 passed 或 failed。" style={{ marginBottom: 16 }} />}
      {error && <Alert type="error" showIcon message="本地演练操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}
      {loading ? <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载冻结演练清单" /></div> : !data.id && !error ? <Empty description="演练run不存在" /> : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}><Descriptions.Item label="状态"><Tag color={rehearsalRunColor(data.status)}>{rehearsalRunStatusCopy(data.status)}</Tag></Descriptions.Item><Descriptions.Item label="回滚门"><Tag color={rollbackGate ? "red" : "green"}>{rollbackGate ? "REQUIRED" : "未触发"}</Tag></Descriptions.Item><Descriptions.Item label="开始时间">{formatP7DateTime(data.started_at)}</Descriptions.Item><Descriptions.Item label="完成时间">{formatP7DateTime(data.completed_at)}</Descriptions.Item></Descriptions>
          <div style={{ display: "grid", gridTemplateColumns: screens.md ? "repeat(5, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))", borderTop: "1px solid #f0f0f0", borderLeft: "1px solid #f0f0f0" }}>
            {([[
              "总项数", data.total_count,
            ], ["待记录", data.pending_count], ["通过", data.passed_count], ["失败", data.failed_count], ["阻断", data.blocked_count]] as const).map(([label, value]) => <div key={label} style={{ padding: 16, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}><Statistic title={label} value={value} valueStyle={(label === "失败" || label === "阻断") && value > 0 ? { color: "#cf1322" } : undefined} /></div>)}
          </div>
          <section aria-labelledby="p7-results-heading"><Typography.Title id="p7-results-heading" level={4}>冻结检查结果</Typography.Title><Typography.Paragraph type="secondary">每项只能从 pending 首次记录为 passed、failed 或 blocked；终态不可改写。</Typography.Paragraph>{data.results.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该run没有冻结检查项" /> : screens.md ? <Table<RehearsalCheckResult> rowKey="id" dataSource={data.results} columns={columns} pagination={false} scroll={{ x: 1180 }} /> : <List dataSource={data.results} renderItem={(result) => <List.Item><div style={{ width: "100%" }}><Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{result.sequence_no}. {result.label}</Typography.Text><Tag color={rehearsalResultColor(result.status)}>{rehearsalResultStatusCopy(result.status)}</Tag></Space><Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{rehearsalCategoryCopy(result.category)} · {result.required ? "必需" : "可选"} · {p7ReasonCopy(result.reason_code)}</Typography.Paragraph><Space wrap>{result.allowed_actions.includes("view") && <Button onClick={() => setInspectTarget(result)}>查看证据</Button>}{result.allowed_actions.includes("record") && <Button type="primary" onClick={() => setRecordTarget(result)}>记录结果</Button>}</Space></div></List.Item>} />}</section>
        </Space>
      )}
      <RehearsalResultModal open={Boolean(recordTarget)} result={recordTarget} onCancel={() => setRecordTarget(null)} onSubmit={recordResult} />
      <RehearsalRunActionModal open={Boolean(runAction)} action={runAction ?? "complete"} rollbackRequired={rollbackGate} onCancel={() => setRunAction(null)} onConfirm={closeRun} />
      <Drawer open={Boolean(inspectTarget)} width="min(640px, 100vw)" title="不可变检查证据" onClose={() => setInspectTarget(null)}>{inspectTarget && <Descriptions bordered size="small" column={1}><Descriptions.Item label="冻结检查键"><Typography.Text code>{inspectTarget.check_key}</Typography.Text></Descriptions.Item><Descriptions.Item label="结论"><Tag color={rehearsalResultColor(inspectTarget.status)}>{rehearsalResultStatusCopy(inspectTarget.status)}</Tag></Descriptions.Item><Descriptions.Item label="原因">{p7ReasonCopy(inspectTarget.reason_code)}</Descriptions.Item><Descriptions.Item label="证据SHA-256"><Typography.Text code style={{ overflowWrap: "anywhere" }}>{inspectTarget.evidence_sha256 ?? "—"}</Typography.Text></Descriptions.Item><Descriptions.Item label="记录时间">{formatP7DateTime(inspectTarget.recorded_at)}</Descriptions.Item></Descriptions>}</Drawer>
    </div>
  );
}
