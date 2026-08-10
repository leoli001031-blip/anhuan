import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import AssignmentDrawer from "../components/AssignmentDrawer";
import BusinessTimeline from "../components/BusinessTimeline";
import ServiceCaseForm from "../components/ServiceCaseForm";
import SiteVisitPanel from "../components/SiteVisitPanel";
import { closeServiceCase, getServiceCase, updateServiceCase } from "../p2Api";
import type { ServiceCase, ServiceCaseInput } from "../p2Api";

const FINDING_SUMMARY_LABELS: Record<string, string> = {
  total: "全部",
  open: "待处理",
  rectifying: "整改中",
  submitted: "已提交",
  reviewing: "复核中",
  passed: "已通过",
  rejected: "已退回",
  closed: "已关闭",
  overdue: "已逾期",
};

function formatDateTime(value: string | null): string {
  if (!value) return "未安排";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function statusColor(status: string): string {
  if (["closed", "completed"].includes(status)) return "green";
  if (["cancelled", "rejected"].includes(status)) return "red";
  if (["in_progress", "active"].includes(status)) return "blue";
  return "default";
}

function severityColor(severity: string): string {
  if (severity === "critical") return "red";
  if (severity === "high") return "orange";
  if (severity === "medium") return "gold";
  return "blue";
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export default function ServiceCaseDetail() {
  const { caseId } = useParams<{ caseId: string }>();
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [serviceCase, setServiceCase] = useState<ServiceCase | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [assignmentOpen, setAssignmentOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [closing, setClosing] = useState(false);

  const refresh = useCallback(async () => {
    if (!caseId) {
      setError("服务任务标识缺失");
      setLoading(false);
      return;
    }
    if (!getSelectedEnterprise()) {
      setError("请先在顶部选择企业");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setServiceCase(await getServiceCase(getAccessToken(), caseId));
    } catch (reason) {
      setServiceCase(null);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [caseId, getAccessToken]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const assignments = useMemo(
    () => serviceCase?.assignments ?? [],
    [serviceCase?.assignments],
  );
  const canEdit = serviceCase?.allowed_actions.includes("edit") ?? false;
  const canAssign = serviceCase?.allowed_actions.includes("assign") ?? false;
  const canOpenAssignments = canAssign || assignments.length > 0;
  const canPlanVisit = serviceCase?.allowed_actions.includes("plan_visit") ?? false;
  const canClose = serviceCase?.allowed_actions.includes("close") ?? false;
  const siteVisits = serviceCase?.site_visits ?? [];
  const findings = serviceCase?.findings ?? [];
  const findingSummary = serviceCase?.finding_summary ?? {};
  const timeline = serviceCase?.timeline ?? [];

  const update = async (values: ServiceCaseInput) => {
    if (!caseId || !canEdit) return;
    setSaving(true);
    setError(null);
    try {
      await updateServiceCase(getAccessToken(), caseId, values);
      message.success("服务任务已更新");
      setEditOpen(false);
      await refresh();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const closeCase = async () => {
    if (!caseId || !canClose) return;
    setClosing(true);
    setError(null);
    try {
      await closeServiceCase(getAccessToken(), caseId);
      message.success("服务任务已关闭");
      await refresh();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setClosing(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 64, textAlign: "center" }}>
        <Spin tip="正在加载任务详情" />
      </div>
    );
  }

  if (!serviceCase) {
    return (
      <div style={{ textAlign: "left" }}>
        <Alert
          type="error"
          showIcon
          message="无法打开服务任务"
          description={error ?? "任务不存在或当前账号无权查看"}
          action={
            <Space wrap>
              <Button onClick={() => void refresh()}>重试</Button>
              <Button onClick={() => navigate("/service-cases")}>返回列表</Button>
            </Space>
          }
        />
      </div>
    );
  }

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <Space align="center" wrap>
          <Button onClick={() => navigate("/service-cases")}>返回任务列表</Button>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {serviceCase.title}
            </Typography.Title>
            <Typography.Text type="secondary">{serviceCase.service_type}</Typography.Text>
          </div>
        </Space>
        <Space wrap>
          <Button
            onClick={() => navigate(`/findings?caseId=${encodeURIComponent(serviceCase.id)}`)}
          >
            问题整改
          </Button>
          {canEdit && <Button onClick={() => setEditOpen(true)}>编辑任务</Button>}
          {canClose && (
            <Popconfirm
              title="确认关闭服务任务？"
              description="仅在现场服务和问题整改均完成后关闭。"
              onConfirm={closeCase}
            >
              <Button loading={closing}>关闭任务</Button>
            </Popconfirm>
          )}
        </Space>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="最近一次操作未完成"
          description={error}
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card title="任务概览">
          <Descriptions column={{ xs: 1, sm: 2, lg: 3 }}>
            <Descriptions.Item label="状态">
              <Tag color={statusColor(serviceCase.status)}>{serviceCase.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="服务类型">
              {serviceCase.service_type}
            </Descriptions.Item>
            <Descriptions.Item label="分配人数">
              {assignments.length}
            </Descriptions.Item>
            <Descriptions.Item label="计划开始">
              {formatDateTime(serviceCase.planned_start_at)}
            </Descriptions.Item>
            <Descriptions.Item label="计划结束">
              {formatDateTime(serviceCase.planned_end_at)}
            </Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5}>任务说明</Typography.Title>
          {serviceCase.description ? (
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
              {serviceCase.description}
            </Typography.Paragraph>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务说明" />
          )}
        </Card>

        <Card
          title={`人员分配（${assignments.length}）`}
          extra={
            canOpenAssignments ? (
              <Button type="primary" onClick={() => setAssignmentOpen(true)}>
                {canAssign ? "管理分配" : "查看分配"}
              </Button>
            ) : null
          }
        >
          {assignments.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未分配人员" />
          ) : (
            <Space wrap size="middle">
              {assignments.map((assignment) => (
                <Card key={assignment.id} size="small">
                  <Space direction="vertical" size={4}>
                    <Typography.Text code>
                      {shortId(assignment.assignee_user_id)}
                    </Typography.Text>
                    <Space size="small">
                      <Tag>{assignment.capacity}</Tag>
                      <Tag color={statusColor(assignment.status)}>
                        {assignment.status}
                      </Tag>
                    </Space>
                  </Space>
                </Card>
              ))}
            </Space>
          )}
        </Card>

        {caseId && (
          <SiteVisitPanel
            caseId={caseId}
            token={getAccessToken()}
            canPlan={canPlanVisit}
            visits={siteVisits}
            onChanged={refresh}
          />
        )}

        <Card
          title="问题整改"
          extra={
            <Button
              onClick={() =>
                navigate(`/findings?caseId=${encodeURIComponent(serviceCase.id)}`)
              }
            >
              打开问题看板
            </Button>
          }
        >
          {Object.keys(findingSummary).length > 0 && (
            <Space wrap style={{ marginBottom: 16 }}>
              {Object.entries(findingSummary).map(([key, value]) => (
                <Tag key={key} color={key === "overdue" && value > 0 ? "red" : "blue"}>
                  {FINDING_SUMMARY_LABELS[key] ?? key}：{value}
                </Tag>
              ))}
            </Space>
          )}
          {findings.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前任务暂无问题" />
          ) : (
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              {findings.map((finding) => (
                <Card
                  key={finding.id}
                  size="small"
                  onClick={() => navigate(`/findings/${finding.id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <Space
                    align="center"
                    wrap
                    style={{ width: "100%", justifyContent: "space-between" }}
                  >
                    <Typography.Text strong>{finding.title}</Typography.Text>
                    <Space size="small" wrap>
                      <Tag color={severityColor(finding.severity)}>
                        {finding.severity}
                      </Tag>
                      <Tag color={statusColor(finding.status)}>{finding.status}</Tag>
                      <Typography.Text type="secondary">
                        截止 {formatDateTime(finding.due_at)}
                      </Typography.Text>
                    </Space>
                  </Space>
                </Card>
              ))}
            </Space>
          )}
        </Card>

        <BusinessTimeline items={timeline} />
      </Space>

      <Modal
        title="编辑服务任务"
        open={editOpen && canEdit}
        onCancel={() => setEditOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <ServiceCaseForm
          initialValues={serviceCase}
          submitLabel="保存修改"
          submitting={saving}
          onSubmit={update}
          onCancel={() => setEditOpen(false)}
        />
      </Modal>

      {caseId && (
        <AssignmentDrawer
          open={assignmentOpen}
          caseId={caseId}
          token={getAccessToken()}
          canAssign={canAssign}
          assignments={assignments}
          onClose={() => setAssignmentOpen(false)}
          onChanged={refresh}
        />
      )}
    </div>
  );
}
