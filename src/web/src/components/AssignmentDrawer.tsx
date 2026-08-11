import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Form,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  actOnAssignment,
  createAssignment,
  listAssignmentCandidates,
} from "../p2Api";
import type {
  AssignmentCandidate,
  ServiceAssignment,
} from "../p2Api";

interface AssignmentFormValues {
  assignee_user_id: string;
  capacity: string;
}

interface AssignmentDrawerProps {
  open: boolean;
  caseId: string;
  token: string | null;
  canAssign: boolean;
  assignments: ServiceAssignment[];
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}

const ACTION_LABELS: Record<"accept" | "reject" | "revoke", string> = {
  accept: "接受",
  reject: "拒绝",
  revoke: "撤销",
};

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function statusColor(status: string): string {
  if (status === "accepted") return "green";
  if (status === "rejected") return "red";
  if (status === "revoked") return "default";
  return "blue";
}

export default function AssignmentDrawer({
  open,
  caseId,
  token,
  canAssign,
  assignments,
  onClose,
  onChanged,
}: AssignmentDrawerProps) {
  const [form] = Form.useForm<AssignmentFormValues>();
  const selectedUserId = Form.useWatch("assignee_user_id", form);
  const [candidates, setCandidates] = useState<AssignmentCandidate[]>([]);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !canAssign) return;
    setCandidateLoading(true);
    setError(null);
    listAssignmentCandidates(token)
      .then(setCandidates)
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setCandidateLoading(false));
  }, [canAssign, open, token]);

  const capacityOptions = useMemo(() => {
    const candidate = candidates.find((item) => item.user_id === selectedUserId);
    return (candidate?.allowed_capacities ?? []).map((capacity) => ({
      value: capacity,
      label: capacity,
    }));
  }, [candidates, selectedUserId]);

  const assign = async (values: AssignmentFormValues) => {
    setSubmitting(true);
    setError(null);
    try {
      await createAssignment(token, caseId, values);
      form.resetFields();
      message.success("人员已分配");
      await onChanged();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const act = async (
    assignmentId: string,
    action: "accept" | "reject" | "revoke",
  ) => {
    const actionKey = `${assignmentId}:${action}`;
    setActing(actionKey);
    setError(null);
    try {
      await actOnAssignment(token, caseId, assignmentId, action);
      message.success(`分配已${ACTION_LABELS[action]}`);
      await onChanged();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setActing(null);
    }
  };

  return (
    <Drawer
      title="人员分配"
      open={open}
      onClose={onClose}
      width="min(640px, 100vw)"
    >
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        {error && (
          <Alert
            type="error"
            showIcon
            message="操作未完成"
            description={error}
            closable
            onClose={() => setError(null)}
          />
        )}

        {canAssign && (
          <section>
            <Typography.Title level={5}>新增分配</Typography.Title>
            {candidateLoading ? (
              <Spin />
            ) : candidates.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可分配人员" />
            ) : (
              <Form<AssignmentFormValues>
                form={form}
                layout="vertical"
                onFinish={assign}
              >
                <Form.Item
                  name="assignee_user_id"
                  label="执行人员"
                  rules={[{ required: true, message: "请选择执行人员" }]}
                >
                  <Select
                    data-testid="assignment-candidate-select"
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择租户内人员"
                    onChange={() => form.setFieldValue("capacity", undefined)}
                    options={candidates.map((candidate) => ({
                      value: candidate.user_id,
                      label: `${candidate.membership_role} · ${shortId(candidate.user_id)}`,
                    }))}
                  />
                </Form.Item>
                <Form.Item
                  name="capacity"
                  label="任务身份"
                  rules={[{ required: true, message: "请选择任务身份" }]}
                >
                  <Select
                    data-testid="assignment-capacity-select"
                    disabled={!selectedUserId}
                    placeholder="选择员工、顾问或合作伙伴"
                    options={capacityOptions}
                  />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={submitting}>
                  确认分配
                </Button>
              </Form>
            )}
          </section>
        )}

        <section>
          <Typography.Title level={5}>当前分配</Typography.Title>
          {assignments.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未分配人员" />
          ) : (
            <Table<ServiceAssignment>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={assignments}
              scroll={{ x: 560 }}
              columns={[
                {
                  title: "人员",
                  dataIndex: "assignee_user_id",
                  render: (value: string) => (
                    <Typography.Text code>{shortId(value)}</Typography.Text>
                  ),
                },
                { title: "身份", dataIndex: "capacity" },
                {
                  title: "状态",
                  dataIndex: "status",
                  render: (value: string) => (
                    <Tag color={statusColor(value)}>{value}</Tag>
                  ),
                },
                {
                  title: "操作",
                  key: "actions",
                  render: (_, assignment) => (
                    <Space size="small" wrap>
                      {(["accept", "reject", "revoke"] as const)
                        .filter((action) => assignment.allowed_actions.includes(action))
                        .map((action) => (
                          <Button
                            key={action}
                            size="small"
                            danger={action === "reject" || action === "revoke"}
                            loading={acting === `${assignment.id}:${action}`}
                            onClick={() => act(assignment.id, action)}
                          >
                            {ACTION_LABELS[action]}
                          </Button>
                        ))}
                    </Space>
                  ),
                },
              ]}
            />
          )}
        </section>
      </Space>
    </Drawer>
  );
}
