// 运营台 · 客户问题详情（/console/clients/:clientId/rectification/:findingId）。
// 在客户工作区内完成录入→整改→复核→关闭闭环；服务端校验归属。
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import dayjs from "dayjs";
import { useAuth } from "../../auth/OidcProvider";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import StatusDot from "../../components/StatusDot";
import {
  closeFinding,
  getFinding,
  reviewFinding,
  startFindingReview,
  startRectification,
  submitCorrectiveAction,
  updateFinding,
  type Finding,
} from "../../p2FindingsApi";
import { listClientServiceCases, type ServiceCase } from "../../p2Api";
import ClientShell from "./ClientShell";

const SEVERITY_LABEL: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

const STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  rectifying: "整改中",
  in_rectification: "整改中",
  submitted: "已提交",
  reviewing: "待复核",
  in_review: "待复核",
  pending_review: "待复核",
  passed: "已通过",
  rejected: "已退回",
  closed: "已关闭",
};

function statusTone(status: string): "success" | "danger" | "warning" | "processing" | "neutral" {
  if (["passed", "closed"].includes(status)) return "success";
  if (status === "rejected") return "danger";
  if (["submitted", "reviewing", "in_review", "pending_review"].includes(status)) return "warning";
  if (["rectifying", "in_rectification"].includes(status)) return "processing";
  return "neutral";
}

interface CorrectiveAction {
  id: string;
  description: string;
  submitted_at?: string;
  created_at?: string;
  submitted_by_user_id?: string;
}

interface FindingReview {
  id: string;
  decision: string;
  comment: string | null;
  reviewed_at?: string;
  created_at?: string;
  reviewed_by_user_id?: string;
}

export default function ClientFindingDetailPage() {
  const { clientId = "", findingId = "" } = useParams();
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [finding, setFinding] = useState<Finding | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [reviewDecision, setReviewDecision] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();
  const [serviceCases, setServiceCases] = useState<ServiceCase[]>([]);

  const refreshEpoch = useRef(0);

  const refresh = useCallback(async () => {
    if (!findingId || !clientId) return;
    setLoading(true);
    setError(null);
    try {
      const token = getAccessToken();
      if (!token) return;
      const f = await getFinding(token, findingId);
      // 客户归属校验：finding 所属的 service case 必须属于当前客户
      const cases = await listClientServiceCases(token, clientId);
      const caseIds = new Set(cases.items.map((c: ServiceCase) => c.id));
      if (!caseIds.has(f.service_case_id)) {
        setError(new Error("该问题不属于当前客户"));
        setFinding(null);
        return;
      }
      setFinding(f);
      setServiceCases(cases.items);
    } catch (e) {
      setError(e);
      setFinding(null);
    } finally {
      setLoading(false);
    }
  }, [findingId, clientId, getAccessToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const runAction = async (
    key: string,
    successMessage: string,
    operation: () => Promise<Finding>,
  ) => {
    const epoch = refreshEpoch.current;
    setActionLoading(key);
    setError(null);
    try {
      await operation();
      if (refreshEpoch.current !== epoch) return;
      message.success(successMessage);
      await refresh();
    } catch (reason) {
      if (refreshEpoch.current !== epoch) return;
      setError(reason);
      message.error(String(reason));
    } finally {
      if (refreshEpoch.current === epoch) setActionLoading(null);
    }
  };

  const allowed = finding?.allowed_actions ?? [];

  const submitCorrection = async () => {
    if (!findingId || !allowed.includes("submit_correction")) return;
    await runAction("submit_correction", "整改已提交", async () => {
      const result = await submitCorrectiveAction(getAccessToken() ?? "", findingId, correctionText.trim());
      setCorrectionText("");
      setCorrectionOpen(false);
      return result;
    });
  };

  const submitReview = async () => {
    if (!findingId || !reviewDecision) return;
    const expected = reviewDecision === "passed" ? "pass" : "reject";
    if (!allowed.includes(expected)) return;
    await runAction(expected, reviewDecision === "passed" ? "复核已通过" : "整改已退回", async () => {
      const result = await reviewFinding(getAccessToken() ?? "", findingId, reviewDecision as "passed" | "rejected", reviewComment.trim());
      setReviewDecision(null);
      setReviewComment("");
      return result;
    });
  };

  if (loading) {
    return (
      <ClientShell clientId={clientId}>
        <Spin style={{ display: "block", margin: "96px auto" }} />
      </ClientShell>
    );
  }

  if (error) {
    return (
      <ClientShell clientId={clientId}>
        <ErrorState error={error} onRetry={() => void refresh()} />
        <Button onClick={() => navigate(`/console/clients/${clientId}/rectification`)}>
          返回整改列表
        </Button>
      </ClientShell>
    );
  }

  if (!finding) {
    return (
      <ClientShell clientId={clientId}>
        <Alert type="warning" message="问题不存在或不属于当前客户" showIcon />
        <Button
          style={{ marginTop: 16 }}
          onClick={() => navigate(`/console/clients/${clientId}/rectification`)}
        >
          返回整改列表
        </Button>
      </ClientShell>
    );
  }

  const caseTitle = serviceCases.find((c) => c.id === finding.service_case_id)?.title ?? finding.service_case_id;
  const correctiveActions = (finding as Finding & { corrective_actions?: CorrectiveAction[] }).corrective_actions ?? [];
  const reviews = (finding as Finding & { reviews?: FindingReview[] }).reviews ?? [];

  return (
    <ClientShell clientId={clientId}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <Button size="small" onClick={() => navigate(`/console/clients/${clientId}/rectification`)}>
          ← 返回整改列表
        </Button>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {finding.title}
        </Typography.Title>
        <StatusDot tone={statusTone(finding.status)} label={STATUS_LABEL[finding.status] ?? finding.status} />
      </div>

      <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }} style={{ marginBottom: 24 }}>
        <Descriptions.Item label="所属服务">{caseTitle}</Descriptions.Item>
        <Descriptions.Item label="严重度">{SEVERITY_LABEL[finding.severity] ?? finding.severity}</Descriptions.Item>
        <Descriptions.Item label="期限">{finding.due_at ? formatDateTime(finding.due_at) : "未设期限"}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{formatDateTime(finding.created_at ?? "")}</Descriptions.Item>
        {finding.description && (
          <Descriptions.Item label="描述" span={2}>
            {finding.description}
          </Descriptions.Item>
        )}
      </Descriptions>

      <Space wrap style={{ marginBottom: 24 }}>
        {allowed.includes("edit") && (
          <Button onClick={() => setEditOpen(true)}>编辑问题</Button>
        )}
        {allowed.includes("start_rectification") && (
          <Button
            type="primary"
            loading={actionLoading === "start_rectification"}
            onClick={() =>
              void runAction("start_rectification", "已开始整改", () =>
                startRectification(getAccessToken() ?? "", findingId),
              )
            }
          >
            开始整改
          </Button>
        )}
        {allowed.includes("submit_correction") && (
          <Button type="primary" onClick={() => setCorrectionOpen(true)}>
            提交整改
          </Button>
        )}
        {allowed.includes("start_review") && (
          <Button
            loading={actionLoading === "start_review"}
            onClick={() =>
              void runAction("start_review", "已进入复核", () =>
                startFindingReview(getAccessToken() ?? "", findingId),
              )
            }
          >
            进入复核
          </Button>
        )}
        {allowed.includes("pass") && (
          <Button type="primary" onClick={() => setReviewDecision("passed")}>
            复核通过
          </Button>
        )}
        {allowed.includes("reject") && (
          <Button danger onClick={() => setReviewDecision("rejected")}>
            退回重提
          </Button>
        )}
        {allowed.includes("close") && (
          <Button
            loading={actionLoading === "close"}
            onClick={() =>
              void runAction("close", "问题已关闭", () =>
                closeFinding(getAccessToken() ?? "", findingId),
              )
            }
          >
            关闭问题
          </Button>
        )}
      </Space>

      {correctiveActions.length > 0 && (
        <>
          <Typography.Title level={5}>整改记录</Typography.Title>
          {correctiveActions.map((action, i) => (
            <div key={action.id || i} style={{ marginBottom: 8, padding: "8px 12px", background: "#fafafa" }}>
              <Typography.Text>{action.description}</Typography.Text>
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatDateTime(action.submitted_at ?? action.created_at ?? "")} · {action.submitted_by_user_id ?? "—"}
                </Typography.Text>
              </div>
            </div>
          ))}
        </>
      )}

      {reviews.length > 0 && (
        <>
          <Typography.Title level={5} style={{ marginTop: 16 }}>
            复核记录
          </Typography.Title>
          {reviews.map((review, i) => (
            <div key={review.id || i} style={{ marginBottom: 8, padding: "8px 12px", background: "#fafafa" }}>
              <Space>
                <StatusDot
                  tone={review.decision === "passed" ? "success" : "danger"}
                  label={review.decision === "passed" ? "通过" : "退回"}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatDateTime(review.reviewed_at ?? review.created_at ?? "")} · {review.reviewed_by_user_id ?? "—"}
                </Typography.Text>
              </Space>
              {review.comment && (
                <div>
                  <Typography.Text>{review.comment}</Typography.Text>
                </div>
              )}
            </div>
          ))}
        </>
      )}

      {/* 提交整改弹窗 */}
      <Modal
        title="提交整改说明"
        open={correctionOpen}
        onOk={() => void submitCorrection()}
        onCancel={() => setCorrectionOpen(false)}
        okText="提交"
        cancelText="取消"
        okButtonProps={{ disabled: !correctionText.trim() }}
      >
        <Input.TextArea
          rows={4}
          value={correctionText}
          onChange={(e) => setCorrectionText(e.target.value)}
          placeholder="描述整改措施与结果"
        />
      </Modal>

      {/* 复核弹窗 */}
      <Modal
        title={reviewDecision === "passed" ? "确认通过" : "确认退回"}
        open={!!reviewDecision}
        onOk={() => void submitReview()}
        onCancel={() => {
          setReviewDecision(null);
          setReviewComment("");
        }}
        okText={reviewDecision === "passed" ? "通过" : "退回"}
        cancelText="取消"
      >
        <Input.TextArea
          rows={3}
          value={reviewComment}
          onChange={(e) => setReviewComment(e.target.value)}
          placeholder={reviewDecision === "passed" ? "复核意见（可选）" : "退回原因"}
        />
      </Modal>

      {/* 编辑弹窗 */}
      <Drawer
        title="编辑问题"
        open={editOpen}
        onClose={() => setEditOpen(false)}
        width={480}
      >
        <Form
          form={editForm}
          layout="vertical"
          initialValues={{
            title: finding.title,
            description: finding.description,
            severity: finding.severity,
          }}
          onFinish={async (values) => {
            await runAction("edit", "问题信息已更新", async () => {
              const result = await updateFinding(getAccessToken() ?? "", findingId, {
                title: values.title,
                description: values.description ?? null,
                severity: values.severity,
                responsible_user_id: finding.responsible_user_id,
                due_at: values.due_at?.toISOString() ?? finding.due_at,
              });
              setEditOpen(false);
              return result;
            });
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="severity" label="严重度" rules={[{ required: true }]}>
            <Select>
              {Object.entries(SEVERITY_LABEL).map(([value, label]) => (
                <Select.Option key={value} value={value}>{label}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="due_at" label="期限" initialValue={finding.due_at ? dayjs(finding.due_at) : undefined}>
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={actionLoading === "edit"}>
            保存
          </Button>
        </Form>
      </Drawer>
    </ClientShell>
  );
}
