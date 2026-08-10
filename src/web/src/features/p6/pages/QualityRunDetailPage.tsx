import { useCallback, useEffect, useState } from "react";
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
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { getQualityRun, isAutomatedQualityRequestAborted, reviewQualityDisagreement, userFacingAutomatedQualityError } from "../automatedQualityApi";
import DisagreementReviewModal from "../components/DisagreementReviewModal";
import LimitedMetrics from "../components/LimitedMetrics";
import P6BoundaryBanner from "../components/P6BoundaryBanner";
import { useP6TenantQuery } from "../hooks/useP6TenantQuery";
import {
  disagreementKindCopy,
  disagreementReviewColor,
  disagreementReviewCopy,
  formatP6DateTime,
  p6ReasonCopy,
  qualityResultStatusCopy,
  qualityRunStatusCopy,
  resultStatusColor,
  runStatusColor,
} from "../reasonCopy";
import type { QualityDisagreement, QualityResult, QualityRunDetail, ReviewDisagreementInput } from "../types";

const EMPTY_RUN: QualityRunDetail = {
  id: "",
  enterprise_id: "",
  suite_id: "",
  status: "queued",
  trigger_kind: "manual",
  total_count: 0,
  passed_count: 0,
  failed_count: 0,
  error_count: 0,
  created_by_user_id: "",
  created_at: "",
  started_at: null,
  completed_at: null,
  allowed_actions: [],
  results: [],
};

type ReviewAction = "acknowledged" | "waived";

export default function QualityRunDetailPage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [inspectedResult, setInspectedResult] = useState<QualityResult | null>(null);
  const [reviewTarget, setReviewTarget] = useState<QualityDisagreement | null>(null);
  const [reviewAction, setReviewAction] = useState<ReviewAction>("acknowledged");
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getQualityRun(token, runId, signal),
    [runId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP6TenantQuery(EMPTY_RUN, load);

  useEffect(() => {
    setInspectedResult(null);
    setReviewTarget(null);
  }, [tenantEpoch]);

  const openReview = (item: QualityDisagreement, action: ReviewAction) => {
    if (!item.allowed_actions.includes(action === "acknowledged" ? "acknowledge" : "waive")) return;
    setReviewTarget(item);
    setReviewAction(action);
  };

  const handleReview = async (input: ReviewDisagreementInput) => {
    if (!reviewTarget) return;
    setError(null);
    try {
      await runMutation((token, signal) =>
        reviewQualityDisagreement(token, reviewTarget.id, input, signal),
      );
      setReviewTarget(null);
      setInspectedResult(null);
      await reload();
    } catch (reason) {
      if (!isAutomatedQualityRequestAborted(reason)) setError(userFacingAutomatedQualityError(reason));
    }
  };

  const resultColumns: TableColumnsType<QualityResult> = [
    { title: "场景", dataIndex: "scenario_id", width: 180, render: (value: string) => `…${value.slice(-8)}` },
    {
      title: "结果",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <Tag color={resultStatusColor(value)}>{qualityResultStatusCopy(value)}</Tag>,
    },
    { title: "原因码", dataIndex: "reason_code", width: 240, render: (value: string) => p6ReasonCopy(value) },
    { title: "证据SHA", dataIndex: "evidence_sha256", width: 260, ellipsis: true },
    { title: "分歧", dataIndex: "disagreements", width: 90, render: (value: QualityDisagreement[]) => value.length },
    { title: "时间", dataIndex: "created_at", width: 180, render: formatP6DateTime },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 110,
      render: (_, result) => result.allowed_actions.includes("view") ? <Button type="link" onClick={() => setInspectedResult(result)}>查看证据</Button> : null,
    },
  ];

  const renderDisagreement = (item: QualityDisagreement) => (
    <List.Item>
      <div style={{ width: "100%" }}>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Typography.Text strong>{disagreementKindCopy(item.kind)}</Typography.Text>
          <Tag color={disagreementReviewColor(item.review_status)}>{disagreementReviewCopy(item.review_status)}</Tag>
        </Space>
        <Descriptions size="small" column={1} style={{ marginTop: 10 }}>
          <Descriptions.Item label="分歧分数">{item.score}</Descriptions.Item>
          <Descriptions.Item label="左侧摘要"><Typography.Text code>{item.left_digest}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="右侧摘要"><Typography.Text code>{item.right_digest}</Typography.Text></Descriptions.Item>
        </Descriptions>
        <Space wrap>
          {item.allowed_actions.includes("acknowledge") && <Button onClick={() => openReview(item, "acknowledged")}>确认分歧</Button>}
          {item.allowed_actions.includes("waive") && <Button danger onClick={() => openReview(item, "waived")}>记录豁免</Button>}
        </Space>
      </div>
    </List.Item>
  );

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/quality/suites/" + data.suite_id)}>← 返回质量套件</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>合成运行 …{runId.slice(-8)}</Typography.Title>
            {!loading && <Tag color={runStatusColor(data.status)}>{qualityRunStatusCopy(data.status)}</Tag>}
          </Space>
        </div>
        <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
      </Space>

      <P6BoundaryBanner />

      {error && <Alert type="error" showIcon message="质量运行加载失败" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载不可变结果" /></div>
      ) : !data.id && !error ? (
        <Empty description="质量运行不存在" />
      ) : (
        <Space direction="vertical" size={28} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="运行状态"><Tag color={runStatusColor(data.status)}>{qualityRunStatusCopy(data.status)}</Tag></Descriptions.Item>
            <Descriptions.Item label="触发方式">本地手动 Oracle</Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatP6DateTime(data.started_at)}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{formatP6DateTime(data.completed_at)}</Descriptions.Item>
          </Descriptions>

          <div style={{ display: "grid", gridTemplateColumns: screens.md ? "repeat(4, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))", borderTop: "1px solid #f0f0f0", borderLeft: "1px solid #f0f0f0" }}>
            {([[
              "场景总数", data.total_count,
            ], ["通过", data.passed_count], ["失败", data.failed_count], ["错误", data.error_count]] as const).map(([label, value]) => (
              <div key={String(label)} style={{ padding: screens.md ? 20 : 14, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}><Statistic title={label} value={value} /></div>
            ))}
          </div>

          <section aria-labelledby="p6-results-heading">
            <Typography.Title id="p6-results-heading" level={4}>不可变结果</Typography.Title>
            <Alert type="info" showIcon message="人工处置只改变分歧状态，不会改变 result 的 failed/error 判定。" style={{ marginBottom: 16 }} />
            {data.results.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前运行没有结果" /> : screens.md ? (
              <Table<QualityResult> rowKey="id" dataSource={data.results} columns={resultColumns} pagination={false} scroll={{ x: 1190 }} />
            ) : (
              <List dataSource={data.results} renderItem={(result) => (
                <List.Item>
                  <div style={{ width: "100%" }}>
                    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>场景 …{result.scenario_id.slice(-8)}</Typography.Text><Tag color={resultStatusColor(result.status)}>{qualityResultStatusCopy(result.status)}</Tag></Space>
                    <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>{p6ReasonCopy(result.reason_code)} · {result.disagreements.length} 个分歧</Typography.Paragraph>
                    {result.allowed_actions.includes("view") && <Button block onClick={() => setInspectedResult(result)}>查看有限证据</Button>}
                  </div>
                </List.Item>
              )} />
            )}
          </section>
        </Space>
      )}

      <Drawer open={Boolean(inspectedResult)} width="min(720px, 100vw)" title="有限结构化结果证据" onClose={() => setInspectedResult(null)}>
        {inspectedResult && (
          <Space direction="vertical" size={20} style={{ width: "100%" }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="判定"><Tag color={resultStatusColor(inspectedResult.status)}>{qualityResultStatusCopy(inspectedResult.status)}</Tag></Descriptions.Item>
              <Descriptions.Item label="原因">{p6ReasonCopy(inspectedResult.reason_code)}</Descriptions.Item>
              <Descriptions.Item label="证据SHA-256"><Typography.Text code style={{ overflowWrap: "anywhere" }}>{inspectedResult.evidence_sha256}</Typography.Text></Descriptions.Item>
            </Descriptions>
            <section><Typography.Title level={5}>有限观察指标</Typography.Title><LimitedMetrics metrics={inspectedResult.observed_metrics} /></section>
            <section>
              <Typography.Title level={5}>分歧</Typography.Title>
              {inspectedResult.disagreements.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该结果没有分歧" /> : <List bordered dataSource={inspectedResult.disagreements} renderItem={renderDisagreement} />}
            </section>
          </Space>
        )}
      </Drawer>

      <DisagreementReviewModal open={Boolean(reviewTarget)} reviewStatus={reviewAction} onCancel={() => setReviewTarget(null)} onSubmit={handleReview} />
    </div>
  );
}
