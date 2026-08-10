import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import SiteVisitForm from "./SiteVisitForm";
import {
  actOnSiteVisit,
  createSiteVisit,
  updateSiteVisit,
} from "../p2Api";
import type { SiteVisit, SiteVisitInput } from "../p2Api";

interface SiteVisitPanelProps {
  caseId: string;
  token: string | null;
  canPlan: boolean;
  visits: SiteVisit[];
  onChanged: () => Promise<void> | void;
}

function formatDateTime(value: string | null): string {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function statusColor(status: string): string {
  if (status === "completed") return "green";
  if (status === "in_progress") return "blue";
  if (status === "cancelled") return "red";
  return "default";
}

export default function SiteVisitPanel({
  caseId,
  token,
  canPlan,
  visits,
  onChanged,
}: SiteVisitPanelProps) {
  const [formOpen, setFormOpen] = useState(false);
  const [editingVisit, setEditingVisit] = useState<SiteVisit | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const openCreate = () => {
    setEditingVisit(null);
    setFormOpen(true);
  };

  const openEdit = (visit: SiteVisit) => {
    if (!visit.allowed_actions.includes("edit_visit")) return;
    setEditingVisit(visit);
    setFormOpen(true);
  };

  const save = async (values: SiteVisitInput) => {
    if (editingVisit) {
      if (!editingVisit.allowed_actions.includes("edit_visit")) return;
    } else if (!canPlan) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      if (editingVisit) {
        await updateSiteVisit(token, caseId, editingVisit.id, values);
        message.success("现场服务计划已更新");
      } else {
        await createSiteVisit(token, caseId, values);
        message.success("现场服务已安排");
      }
      setFormOpen(false);
      setEditingVisit(null);
      await onChanged();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const act = async (visit: SiteVisit, action: "start" | "complete") => {
    const requiredAction = action === "start" ? "start_visit" : "complete_visit";
    if (!visit.allowed_actions.includes(requiredAction)) return;
    const actionKey = `${visit.id}:${action}`;
    setActing(actionKey);
    setError(null);
    try {
      await actOnSiteVisit(token, caseId, visit.id, action);
      message.success(action === "start" ? "现场服务已开始" : "现场服务已完成");
      await onChanged();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setActing(null);
    }
  };

  return (
    <Card
      title="现场服务"
      extra={
        canPlan ? (
          <Button type="primary" onClick={openCreate}>
            安排现场服务
          </Button>
        ) : null
      }
    >
      {error && (
        <Alert
          type="error"
          showIcon
          message="现场服务操作未完成"
          description={error}
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {visits.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未安排现场服务">
          {canPlan && (
            <Button type="primary" onClick={openCreate}>
              安排第一次现场服务
            </Button>
          )}
        </Empty>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {visits.map((visit, index) => (
            <Card
              key={visit.id}
              size="small"
              title={
                <Space wrap>
                  <span>现场服务 {index + 1}</span>
                  <Tag color={statusColor(visit.status)}>{visit.status}</Tag>
                </Space>
              }
              extra={
                <Space wrap>
                  {visit.allowed_actions.includes("edit_visit") && (
                    <Button size="small" onClick={() => openEdit(visit)}>
                      编辑计划
                    </Button>
                  )}
                  {visit.allowed_actions.includes("start_visit") && (
                    <Button
                      size="small"
                      type="primary"
                      loading={acting === `${visit.id}:start`}
                      onClick={() => void act(visit, "start")}
                    >
                      开始服务
                    </Button>
                  )}
                  {visit.allowed_actions.includes("complete_visit") && (
                    <Button
                      size="small"
                      type="primary"
                      loading={acting === `${visit.id}:complete`}
                      onClick={() => void act(visit, "complete")}
                    >
                      完成服务
                    </Button>
                  )}
                </Space>
              }
            >
              <Descriptions column={{ xs: 1, sm: 2, lg: 4 }} size="small">
                <Descriptions.Item label="计划开始">
                  {formatDateTime(visit.planned_start_at)}
                </Descriptions.Item>
                <Descriptions.Item label="计划结束">
                  {formatDateTime(visit.planned_end_at)}
                </Descriptions.Item>
                <Descriptions.Item label="实际开始">
                  {formatDateTime(visit.started_at)}
                </Descriptions.Item>
                <Descriptions.Item label="实际完成">
                  {formatDateTime(visit.completed_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </Space>
      )}

      <Modal
        title={editingVisit ? "编辑现场服务计划" : "安排现场服务"}
        open={formOpen}
        onCancel={() => {
          setFormOpen(false);
          setEditingVisit(null);
        }}
        footer={null}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">
          计划提交后，获授权的执行人员可开始并完成现场服务。
        </Typography.Paragraph>
        <SiteVisitForm
          initialValues={editingVisit ?? undefined}
          submitLabel={editingVisit ? "保存计划" : "确认安排"}
          submitting={submitting}
          onSubmit={save}
          onCancel={() => {
            setFormOpen(false);
            setEditingVisit(null);
          }}
        />
      </Modal>
    </Card>
  );
}
