import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import FindingForm from "../components/FindingForm";
import {
  closeFinding,
  getFinding,
  reviewFinding,
  startFindingReview,
  startRectification,
  submitCorrectiveAction,
  updateFinding,
} from "../p2FindingsApi";
import type {
  Finding,
  FindingCreateInput,
  FindingScope,
} from "../p2FindingsApi";

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "未设置";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function shortId(value: string | null): string {
  if (!value) return "未指定";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function severityColor(severity: string): string {
  if (severity === "critical") return "red";
  if (severity === "high") return "orange";
  if (severity === "medium") return "gold";
  return "blue";
}

function statusColor(status: string): string {
  if (["passed", "closed"].includes(status)) return "green";
  if (status === "rejected") return "red";
  if (["submitted", "reviewing"].includes(status)) return "purple";
  if (status === "rectifying") return "blue";
  return "default";
}

function scopeRoute(scope: FindingScope): string {
  if (scope === "rectification") return "/rectification";
  if (scope === "review") return "/reviews";
  return "/findings";
}

export default function FindingDetail() {
  const { findingId } = useParams<{ findingId: string }>();
  const [searchParams] = useSearchParams();
  const requestedScope = searchParams.get("scope");
  const returnScope: FindingScope =
    requestedScope === "rectification" || requestedScope === "review"
      ? requestedScope
      : "all";
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [finding, setFinding] = useState<Finding | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [reviewDecision, setReviewDecision] = useState<"passed" | "rejected" | null>(null);
  const [reviewComment, setReviewComment] = useState("");

  const refresh = useCallback(async () => {
    if (!findingId) {
      setError("问题标识缺失");
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
      setFinding(await getFinding(getAccessToken(), findingId));
    } catch (reason) {
      setFinding(null);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [findingId, getAccessToken]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const allowedActions = finding?.allowed_actions ?? [];
  const correctiveActions = useMemo(
    () => finding?.corrective_actions ?? [],
    [finding?.corrective_actions],
  );
  const reviews = useMemo(() => finding?.reviews ?? [], [finding?.reviews]);

  const runAction = async (
    key: string,
    successMessage: string,
    operation: () => Promise<Finding>,
  ) => {
    setActionLoading(key);
    setError(null);
    try {
      await operation();
      message.success(successMessage);
      await refresh();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setActionLoading(null);
    }
  };

  const saveEdit = async (values: FindingCreateInput) => {
    if (!findingId || !allowedActions.includes("edit")) return;
    await runAction("edit", "问题信息已更新", async () => {
      const result = await updateFinding(getAccessToken(), findingId, {
        title: values.title,
        description: values.description,
        severity: values.severity,
        responsible_user_id: values.responsible_user_id,
        due_at: values.due_at,
      });
      setEditOpen(false);
      return result;
    });
  };

  const submitCorrection = async () => {
    if (!findingId || !allowedActions.includes("submit_correction")) return;
    await runAction("submit_correction", "整改已提交", async () => {
      const result = await submitCorrectiveAction(
        getAccessToken(),
        findingId,
        correctionText.trim(),
      );
      setCorrectionText("");
      setCorrectionOpen(false);
      return result;
    });
  };

  const submitReview = async () => {
    if (!findingId || !reviewDecision) return;
    const expectedAction = reviewDecision === "passed" ? "pass" : "reject";
    if (!allowedActions.includes(expectedAction)) return;
    await runAction(expectedAction, reviewDecision === "passed" ? "复核已通过" : "整改已退回", async () => {
      const result = await reviewFinding(
        getAccessToken(),
        findingId,
        reviewDecision,
        reviewComment.trim(),
      );
      setReviewDecision(null);
      setReviewComment("");
      return result;
    });
  };

  if (loading) {
    return (
      <div style={{ padding: 64, textAlign: "center" }}>
        <Spin tip="正在加载问题详情" />
      </div>
    );
  }

  if (!finding) {
    return (
      <div style={{ textAlign: "left" }}>
        <Alert
          type="error"
          showIcon
          message="无法打开问题"
          description={error ?? "问题不存在或当前账号无权查看"}
          action={
            <Space wrap>
              <Button onClick={() => void refresh()}>重试</Button>
              <Button onClick={() => navigate(scopeRoute(returnScope))}>返回列表</Button>
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
          <Button onClick={() => navigate(scopeRoute(returnScope))}>返回问题列表</Button>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {finding.title}
            </Typography.Title>
            <Space size="small" wrap>
              <Tag color={severityColor(finding.severity)}>{finding.severity}</Tag>
              <Tag color={statusColor(finding.status)}>{finding.status}</Tag>
            </Space>
          </div>
        </Space>
        <Space wrap>
          {allowedActions.includes("edit") && (
            <Button onClick={() => setEditOpen(true)}>编辑问题</Button>
          )}
          {allowedActions.includes("start_rectification") && (
            <Button
              loading={actionLoading === "start_rectification"}
              onClick={() =>
                findingId &&
                void runAction("start_rectification", "已进入整改", () =>
                  startRectification(getAccessToken(), findingId),
                )
              }
            >
              开始整改
            </Button>
          )}
          {allowedActions.includes("submit_correction") && (
            <Button type="primary" onClick={() => setCorrectionOpen(true)}>
              提交整改
            </Button>
          )}
          {allowedActions.includes("start_review") && (
            <Button
              type="primary"
              loading={actionLoading === "start_review"}
              onClick={() =>
                findingId &&
                void runAction("start_review", "已进入复核", () =>
                  startFindingReview(getAccessToken(), findingId),
                )
              }
            >
              开始复核
            </Button>
          )}
          {allowedActions.includes("pass") && (
            <Button type="primary" onClick={() => setReviewDecision("passed")}>
              通过复核
            </Button>
          )}
          {allowedActions.includes("reject") && (
            <Button danger onClick={() => setReviewDecision("rejected")}>
              退回整改
            </Button>
          )}
          {allowedActions.includes("close") && (
            <Popconfirm
              title="确认关闭该问题？"
              description="关闭后将作为本问题整改链路的终态。"
              onConfirm={() => {
                if (!findingId) return Promise.resolve();
                return runAction("close", "问题已关闭", () =>
                  closeFinding(getAccessToken(), findingId),
                );
              }}
            >
              <Button loading={actionLoading === "close"}>关闭问题</Button>
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
        <Card title="问题概览">
          <Descriptions column={{ xs: 1, sm: 2, lg: 3 }}>
            <Descriptions.Item label="严重程度">
              <Tag color={severityColor(finding.severity)}>{finding.severity}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColor(finding.status)}>{finding.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="责任人">
              {shortId(finding.responsible_user_id)}
            </Descriptions.Item>
            <Descriptions.Item label="整改截止">
              {formatDateTime(finding.due_at)}
            </Descriptions.Item>
            <Descriptions.Item label="所属服务任务">
              <Button
                type="link"
                onClick={() => navigate(`/service-cases/${finding.service_case_id}`)}
              >
                打开服务任务
              </Button>
            </Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5}>问题说明</Typography.Title>
          {finding.description ? (
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
              {finding.description}
            </Typography.Paragraph>
          ) : (
            <Typography.Text type="secondary">暂无问题说明</Typography.Text>
          )}
        </Card>

        <Card title={`整改记录（${correctiveActions.length}）`}>
          {correctiveActions.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未提交整改" />
          ) : (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              {correctiveActions.map((item, index) => (
                <Card
                  key={item.id}
                  size="small"
                  title={`第 ${item.revision ?? index + 1} 次提交`}
                >
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                    {item.description}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary">
                    {formatDateTime(item.submitted_at ?? item.created_at)}
                  </Typography.Text>
                </Card>
              ))}
            </Space>
          )}
        </Card>

        <Card title={`复核记录（${reviews.length}）`}>
          {reviews.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无复核记录" />
          ) : (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              {reviews.map((item, index) => (
                <Card
                  key={item.id}
                  size="small"
                  title={
                    <Space>
                      <span>第 {index + 1} 次复核</span>
                      <Tag color={item.decision === "passed" ? "green" : "red"}>
                        {item.decision}
                      </Tag>
                    </Space>
                  }
                >
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                    {item.comment || "无补充意见"}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary">
                    {formatDateTime(item.reviewed_at ?? item.created_at)}
                  </Typography.Text>
                </Card>
              ))}
            </Space>
          )}
        </Card>
      </Space>

      <Modal
        title="编辑问题"
        open={editOpen && allowedActions.includes("edit")}
        onCancel={() => setEditOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <FindingForm
          caseOptions={[{ value: finding.service_case_id, label: "当前服务任务" }]}
          lockedCaseId={finding.service_case_id}
          initialValues={finding}
          submitLabel="保存修改"
          submitting={actionLoading === "edit"}
          onSubmit={saveEdit}
          onCancel={() => setEditOpen(false)}
        />
      </Modal>

      <Modal
        title="提交整改"
        open={correctionOpen && allowedActions.includes("submit_correction")}
        onCancel={() => setCorrectionOpen(false)}
        onOk={() => void submitCorrection()}
        okText="提交整改"
        confirmLoading={actionLoading === "submit_correction"}
        okButtonProps={{ disabled: !correctionText.trim() }}
      >
        <Typography.Paragraph type="secondary">
          说明已完成的整改措施；被退回后可再次提交。
        </Typography.Paragraph>
        <Input.TextArea
          rows={5}
          value={correctionText}
          onChange={(event) => setCorrectionText(event.target.value)}
          placeholder="填写整改措施和完成情况"
        />
      </Modal>

      <Modal
        title={reviewDecision === "passed" ? "通过复核" : "退回整改"}
        open={reviewDecision !== null}
        onCancel={() => setReviewDecision(null)}
        onOk={() => void submitReview()}
        okText={reviewDecision === "passed" ? "确认通过" : "确认退回"}
        okButtonProps={{
          danger: reviewDecision === "rejected",
          disabled: reviewDecision === "rejected" && !reviewComment.trim(),
        }}
        confirmLoading={
          actionLoading === "pass" || actionLoading === "reject"
        }
      >
        <Typography.Paragraph type="secondary">
          {reviewDecision === "passed"
            ? "可填写复核意见后通过。"
            : "退回时必须说明需要补充整改的原因。"}
        </Typography.Paragraph>
        <Input.TextArea
          rows={4}
          value={reviewComment}
          onChange={(event) => setReviewComment(event.target.value)}
          placeholder="填写复核意见"
        />
      </Modal>
    </div>
  );
}
