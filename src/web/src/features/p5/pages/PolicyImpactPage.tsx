import { useCallback, useEffect, useRef, useState } from "react";
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
import { useLocation } from "react-router-dom";
import ImpactTaskModal from "../components/ImpactTaskModal";
import P5BoundaryBanner from "../components/P5BoundaryBanner";
import PolicyImpactModal from "../components/PolicyImpactModal";
import { useP5TenantQuery } from "../hooks/useP5TenantQuery";
import {
  formatP5DateTime,
  impactPriorityCopy,
  impactStatusCopy,
  impactTaskStatusCopy,
  policyDomainCopy,
  priorityColor,
} from "../reasonCopy";
import type {
  CreateImpactTaskInput,
  CreatePolicyImpactInput,
  ImpactStatus,
  ImpactTaskStatus,
  PolicyImpact,
  PolicyImpactCollection,
  PolicyImpactDetail,
  PolicyImpactTask,
  UpdateImpactTaskInput,
  UpdatePolicyImpactInput,
} from "../types";
import {
  createImpactTask,
  createPolicyImpact,
  getPolicyImpact,
  isPolicyWorkflowRequestAborted,
  listPolicyImpacts,
  updateImpactTask,
  updatePolicyImpact,
  userFacingPolicyWorkflowError,
} from "../policyWorkflowApi";

const EMPTY_IMPACTS: PolicyImpactCollection = { items: [], allowed_actions: [] };

function impactStatusColor(status: string): string {
  if (status === "accepted") return "green";
  if (status === "dismissed") return "default";
  return "gold";
}

function taskStatusColor(status: string): string {
  if (status === "completed") return "green";
  if (status === "in_progress") return "blue";
  if (status === "dismissed") return "default";
  return "gold";
}

export default function PolicyImpactPage() {
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [taskOpen, setTaskOpen] = useState(false);
  const [selected, setSelected] = useState<PolicyImpactDetail | null>(null);
  const [selectedTask, setSelectedTask] = useState<PolicyImpactTask | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const openedFromState = useRef(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => listPolicyImpacts(token, signal),
    [],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP5TenantQuery(EMPTY_IMPACTS, load);

  const loadDetail = useCallback(
    async (impactId: string) => {
      setDetailLoading(true);
      setError(null);
      try {
        const detail = await runMutation((token, signal) => getPolicyImpact(token, impactId, signal));
        setSelected(detail);
      } catch (reason) {
        if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
      } finally {
        setDetailLoading(false);
      }
    },
    [runMutation, setError],
  );

  useEffect(() => {
    setCreateOpen(false);
    setEditOpen(false);
    setTaskOpen(false);
    setSelected(null);
    setSelectedTask(null);
    openedFromState.current = false;
  }, [tenantEpoch]);

  useEffect(() => {
    if (openedFromState.current) return;
    const state = location.state;
    const impactId = state && typeof state === "object" && "impactId" in state && typeof state.impactId === "string" ? state.impactId : null;
    if (impactId) {
      openedFromState.current = true;
      void loadDetail(impactId);
    }
  }, [loadDetail, location.state]);

  const refreshAfterWrite = async (impactId: string) => {
    await reload();
    await loadDetail(impactId);
  };

  const handleCreate = async (input: CreatePolicyImpactInput | UpdatePolicyImpactInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createPolicyImpact(token, input as CreatePolicyImpactInput, signal),
      );
      setCreateOpen(false);
      await refreshAfterWrite(created.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const handleEdit = async (input: CreatePolicyImpactInput | UpdatePolicyImpactInput) => {
    if (!selected) return;
    setError(null);
    try {
      await runMutation((token, signal) =>
        updatePolicyImpact(token, selected.id, input as UpdatePolicyImpactInput, signal),
      );
      setEditOpen(false);
      await refreshAfterWrite(selected.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const setImpactStatus = async (status: ImpactStatus) => {
    if (!selected) return;
    setError(null);
    try {
      await runMutation((token, signal) => updatePolicyImpact(token, selected.id, { status }, signal));
      await refreshAfterWrite(selected.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const handleTaskSubmit = async (input: CreateImpactTaskInput | UpdateImpactTaskInput) => {
    if (!selected) return;
    setError(null);
    try {
      if (selectedTask) {
        await runMutation((token, signal) =>
          updateImpactTask(token, selectedTask.id, input as UpdateImpactTaskInput, signal),
        );
      } else {
        await runMutation((token, signal) =>
          createImpactTask(token, selected.id, input as CreateImpactTaskInput, signal),
        );
      }
      setTaskOpen(false);
      setSelectedTask(null);
      await refreshAfterWrite(selected.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const setTaskStatus = async (task: PolicyImpactTask, status: ImpactTaskStatus) => {
    if (!selected) return;
    setError(null);
    try {
      await runMutation((token, signal) => updateImpactTask(token, task.id, { status }, signal));
      await refreshAfterWrite(selected.id);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const columns: TableColumnsType<PolicyImpact> = [
    { title: "领域", dataIndex: "domain", width: 120, render: policyDomainCopy },
    { title: "候选范围", dataIndex: "scope_note", width: 320, ellipsis: true },
    {
      title: "优先级",
      dataIndex: "priority",
      width: 110,
      render: (value: string) => <Tag color={priorityColor(value)}>{impactPriorityCopy(value)}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => <Tag color={impactStatusColor(value)}>{impactStatusCopy(value)}</Tag>,
    },
    { title: "政策版本 ID", dataIndex: "policy_version_id", width: 240, ellipsis: true },
    { title: "更新时间", dataIndex: "updated_at", width: 180, render: formatP5DateTime },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, impact) =>
        impact.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => void loadDetail(impact.id)}>查看</Button>
        ) : null,
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>政策影响候选</Typography.Title>
          <Typography.Text type="secondary">人工研判候选与站内执行待办，不声明法规适用</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>建立影响候选</Button>}
        </Space>
      </Space>

      <P5BoundaryBanner />

      {error && <Alert type="error" showIcon message="政策影响操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 320, display: "grid", placeItems: "center" }}><Spin tip="正在加载影响候选" /></div>
      ) : data.items.length === 0 && !error ? (
        <Empty description="当前企业尚无影响候选">
          {data.allowed_actions.includes("create") && <Button type="primary" onClick={() => setCreateOpen(true)}>建立第一条候选</Button>}
        </Empty>
      ) : screens.md ? (
        <Table<PolicyImpact> rowKey="id" dataSource={data.items} columns={columns} pagination={false} scroll={{ x: 1190 }} />
      ) : (
        <List dataSource={data.items} renderItem={(impact) => (
          <List.Item>
            <div style={{ width: "100%" }}>
              <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                <Typography.Text strong>{policyDomainCopy(impact.domain)}</Typography.Text>
                <Tag color={priorityColor(impact.priority)}>{impactPriorityCopy(impact.priority)}</Tag>
              </Space>
              <Typography.Paragraph ellipsis={{ rows: 3 }} style={{ margin: "8px 0" }}>{impact.scope_note}</Typography.Paragraph>
              <Tag color={impactStatusColor(impact.status)}>{impactStatusCopy(impact.status)}</Tag>
              {impact.allowed_actions.includes("view") && <Button block style={{ marginTop: 12 }} onClick={() => void loadDetail(impact.id)}>查看候选与待办</Button>}
            </div>
          </List.Item>
        )} />
      )}

      <Drawer
        open={Boolean(selected) || detailLoading}
        width="min(760px, 100vw)"
        title="影响候选与待办"
        onClose={() => { setSelected(null); setDetailLoading(false); }}
        extra={selected && (
          <Space wrap>
            {selected.allowed_actions.includes("edit") && <Button onClick={() => setEditOpen(true)}>编辑</Button>}
            {selected.allowed_actions.includes("accept") && <Button type="primary" onClick={() => void setImpactStatus("accepted")}>接受候选</Button>}
            {selected.allowed_actions.includes("dismiss") && <Button danger onClick={() => void setImpactStatus("dismissed")}>排除候选</Button>}
            {selected.allowed_actions.includes("create_task") && <Button type="primary" onClick={() => { setSelectedTask(null); setTaskOpen(true); }}>建立待办</Button>}
          </Space>
        )}
      >
        {detailLoading || !selected ? (
          <div style={{ minHeight: 260, display: "grid", placeItems: "center" }}><Spin tip="正在加载影响候选" /></div>
        ) : (
          <Space direction="vertical" size={24} style={{ width: "100%" }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="政策版本 ID">{selected.policy_version_id}</Descriptions.Item>
              <Descriptions.Item label="领域候选">{policyDomainCopy(selected.domain)}</Descriptions.Item>
              <Descriptions.Item label="优先级"><Tag color={priorityColor(selected.priority)}>{impactPriorityCopy(selected.priority)}</Tag></Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={impactStatusColor(selected.status)}>{impactStatusCopy(selected.status)}</Tag></Descriptions.Item>
              <Descriptions.Item label="候选范围"><Typography.Text style={{ whiteSpace: "pre-wrap" }}>{selected.scope_note}</Typography.Text></Descriptions.Item>
            </Descriptions>

            <section aria-labelledby="p5-impact-tasks-heading">
              <Typography.Title id="p5-impact-tasks-heading" level={4}>站内影响待办</Typography.Title>
              {selected.tasks.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未建立影响待办" />
              ) : (
                <List bordered dataSource={selected.tasks} renderItem={(task) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Typography.Text strong>{task.title}</Typography.Text>
                        <Tag color={taskStatusColor(task.status)}>{impactTaskStatusCopy(task.status)}</Tag>
                      </Space>
                      <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>负责人：{task.owner_user_id ?? "—"} · 截止：{formatP5DateTime(task.due_at)}</Typography.Paragraph>
                      <Space wrap>
                        {task.allowed_actions.includes("edit") && <Button size="small" onClick={() => { setSelectedTask(task); setTaskOpen(true); }}>编辑</Button>}
                        {task.allowed_actions.includes("start") && <Button size="small" type="primary" onClick={() => void setTaskStatus(task, "in_progress")}>开始</Button>}
                        {task.allowed_actions.includes("complete") && <Button size="small" type="primary" onClick={() => void setTaskStatus(task, "completed")}>完成</Button>}
                        {task.allowed_actions.includes("dismiss") && <Button size="small" danger onClick={() => void setTaskStatus(task, "dismissed")}>关闭</Button>}
                      </Space>
                    </div>
                  </List.Item>
                )} />
              )}
            </section>
          </Space>
        )}
      </Drawer>

      <PolicyImpactModal open={createOpen} onCancel={() => setCreateOpen(false)} onSubmit={handleCreate} />
      <PolicyImpactModal open={editOpen} impact={selected} onCancel={() => setEditOpen(false)} onSubmit={handleEdit} />
      <ImpactTaskModal open={taskOpen} task={selectedTask} onCancel={() => { setTaskOpen(false); setSelectedTask(null); }} onSubmit={handleTaskSubmit} />
    </div>
  );
}
