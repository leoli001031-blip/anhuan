import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  List,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useNavigate, useParams } from "react-router-dom";
import P5BoundaryBanner from "../components/P5BoundaryBanner";
import PolicyImpactModal from "../components/PolicyImpactModal";
import PolicyReviewModal from "../components/PolicyReviewModal";
import { useP5TenantQuery } from "../hooks/useP5TenantQuery";
import {
  effectColor,
  effectStatusCopy,
  formatP5Date,
  formatP5DateTime,
  policyDomainCopy,
  reviewActionCopy,
  workflowColor,
  workflowStatusCopy,
} from "../reasonCopy";
import type {
  CreatePolicyImpactInput,
  PolicyReviewInput,
  PolicyVersionDetail,
  UpdatePolicyImpactInput,
} from "../types";
import {
  createPolicyImpact,
  getPolicyVersion,
  isPolicyWorkflowRequestAborted,
  policyVersionAction,
  userFacingPolicyWorkflowError,
} from "../policyWorkflowApi";

type ReviewAction = "submit" | "approve" | "reject" | "publish";

export default function PolicyVersionDetailPage() {
  const { versionId = "" } = useParams<{ versionId: string }>();
  const navigate = useNavigate();
  const [reviewAction, setReviewAction] = useState<ReviewAction | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getPolicyVersion(token, versionId, signal),
    [versionId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP5TenantQuery<PolicyVersionDetail | null>(null, load);

  useEffect(() => {
    setReviewAction(null);
    setImpactOpen(false);
  }, [tenantEpoch]);

  const handleReview = async (input: PolicyReviewInput) => {
    if (!reviewAction) return;
    setError(null);
    try {
      await runMutation((token, signal) =>
        policyVersionAction(token, versionId, reviewAction, input, signal),
      );
      setReviewAction(null);
      await reload();
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  const handleImpact = async (input: CreatePolicyImpactInput | UpdatePolicyImpactInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createPolicyImpact(token, input as CreatePolicyImpactInput, signal),
      );
      setImpactOpen(false);
      navigate("/policy-impact", { state: { impactId: created.id } });
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) setError(userFacingPolicyWorkflowError(reason));
    }
  };

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate(data ? "/policies/sources/" + data.source_id : "/policies")}>← 返回来源版本</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{data ? `v${data.version_number} · ${data.title}` : "政策版本候选"}</Typography.Title>
            {data && <Tag color={workflowColor(data.workflow_status)}>{workflowStatusCopy(data.workflow_status)}</Tag>}
          </Space>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data?.allowed_actions.includes("submit") && <Button onClick={() => setReviewAction("submit")}>提交审核</Button>}
          {data?.allowed_actions.includes("approve") && <Button type="primary" onClick={() => setReviewAction("approve")}>审核通过</Button>}
          {data?.allowed_actions.includes("reject") && <Button danger onClick={() => setReviewAction("reject")}>退回</Button>}
          {data?.allowed_actions.includes("publish") && <Button type="primary" onClick={() => setReviewAction("publish")}>内部发布</Button>}
          {data?.allowed_actions.includes("create_impact") && <Button onClick={() => setImpactOpen(true)}>建立影响候选</Button>}
        </Space>
      </Space>

      <P5BoundaryBanner />

      {error && <Alert type="error" showIcon message="政策版本操作未完成" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载政策版本候选" /></div>
      ) : !data && !error ? (
        <Empty description="政策版本候选不存在" />
      ) : data ? (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="领域候选">{policyDomainCopy(data.domain)}</Descriptions.Item>
            <Descriptions.Item label="效力候选"><Tag color={effectColor(data.effect_status)}>{effectStatusCopy(data.effect_status)}</Tag></Descriptions.Item>
            <Descriptions.Item label="颁布日期">{formatP5Date(data.issued_on)}</Descriptions.Item>
            <Descriptions.Item label="生效区间">{formatP5Date(data.effective_from)} — {formatP5Date(data.effective_to)}</Descriptions.Item>
            <Descriptions.Item label="受控文档版本 ID">{data.document_version_id ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="受控证据 SHA-256"><Typography.Text code style={{ overflowWrap: "anywhere" }}>{data.document_sha256 ?? "—"}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatP5DateTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="提交人 ID">{data.submitted_by_user_id ?? "—"}</Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p5-version-summary-heading">
            <Typography.Title id="p5-version-summary-heading" level={4}>内部候选摘要</Typography.Title>
            {data.summary ? (
              <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{data.summary}</Typography.Paragraph>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未登记候选摘要" />
            )}
          </section>

          <section aria-labelledby="p5-review-events-heading">
            <Typography.Title id="p5-review-events-heading" level={4}>审核事件</Typography.Title>
            {data.review_events.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无审核事件" />
            ) : (
              <List
                bordered
                dataSource={data.review_events}
                renderItem={(event) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Tag color={event.action === "rejected" ? "red" : event.action === "published" ? "green" : "blue"}>{reviewActionCopy(event.action)}</Tag>
                        <Typography.Text type="secondary">{formatP5DateTime(event.occurred_at)}</Typography.Text>
                      </Space>
                      {event.comment && <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: "10px 0 0" }}>{event.comment}</Typography.Paragraph>}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </section>
        </Space>
      ) : null}

      {reviewAction && <PolicyReviewModal open action={reviewAction} onCancel={() => setReviewAction(null)} onSubmit={handleReview} />}
      <PolicyImpactModal
        open={impactOpen}
        initialVersionId={data?.id}
        initialDomain={data?.domain}
        onCancel={() => setImpactOpen(false)}
        onSubmit={handleImpact}
      />
    </div>
  );
}
