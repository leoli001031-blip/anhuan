import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Descriptions, Empty, Grid, List, Select, Space, Spin, Statistic, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import { isAutomatedQualityRequestAborted, listQualityDisagreements, reviewQualityDisagreement, userFacingAutomatedQualityError } from "../automatedQualityApi";
import DisagreementReviewModal from "../components/DisagreementReviewModal";
import P6BoundaryBanner from "../components/P6BoundaryBanner";
import { useP6TenantQuery } from "../hooks/useP6TenantQuery";
import { disagreementKindCopy, disagreementReviewColor, disagreementReviewCopy, formatP6DateTime } from "../reasonCopy";
import type { DisagreementKind, DisagreementReviewStatus, QualityDisagreement, QualityDisagreementCollection, ReviewDisagreementInput } from "../types";

const EMPTY_DISAGREEMENTS: QualityDisagreementCollection = { items: [], count: 0, open_count: 0 };
type ReviewAction = "acknowledged" | "waived";

export default function QualityDisagreementsPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [kind, setKind] = useState<DisagreementKind | undefined>();
  const [reviewStatus, setReviewStatus] = useState<DisagreementReviewStatus | undefined>();
  const [reviewTarget, setReviewTarget] = useState<QualityDisagreement | null>(null);
  const [reviewAction, setReviewAction] = useState<ReviewAction>("acknowledged");
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => listQualityDisagreements(token, signal),
    [],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP6TenantQuery(EMPTY_DISAGREEMENTS, load);

  useEffect(() => {
    setReviewTarget(null);
    setKind(undefined);
    setReviewStatus(undefined);
  }, [tenantEpoch]);

  const filtered = useMemo(
    () => data.items.filter((item) => (!kind || item.kind === kind) && (!reviewStatus || item.review_status === reviewStatus)),
    [data.items, kind, reviewStatus],
  );

  const openReview = (item: QualityDisagreement, action: ReviewAction) => {
    if (!item.allowed_actions.includes(action === "acknowledged" ? "acknowledge" : "waive")) return;
    setReviewTarget(item);
    setReviewAction(action);
  };

  const handleReview = async (input: ReviewDisagreementInput) => {
    if (!reviewTarget) return;
    setError(null);
    try {
      await runMutation((token, signal) => reviewQualityDisagreement(token, reviewTarget.id, input, signal));
      setReviewTarget(null);
      await reload();
    } catch (reason) {
      if (!isAutomatedQualityRequestAborted(reason)) setError(userFacingAutomatedQualityError(reason));
    }
  };

  const actions = (item: QualityDisagreement) => (
    <Space wrap size="small">
      {item.allowed_actions.includes("acknowledge") && <Button type="link" onClick={() => openReview(item, "acknowledged")}>确认</Button>}
      {item.allowed_actions.includes("waive") && <Button type="link" danger onClick={() => openReview(item, "waived")}>豁免</Button>}
    </Space>
  );

  const columns: TableColumnsType<QualityDisagreement> = [
    { title: "类型", dataIndex: "kind", width: 140, render: disagreementKindCopy },
    { title: "分数", dataIndex: "score", width: 90 },
    { title: "左侧摘要", dataIndex: "left_digest", width: 230, ellipsis: true },
    { title: "右侧摘要", dataIndex: "right_digest", width: 230, ellipsis: true },
    { title: "处置", dataIndex: "review_status", width: 110, render: (value: string) => <Tag color={disagreementReviewColor(value)}>{disagreementReviewCopy(value)}</Tag> },
    { title: "登记时间", dataIndex: "created_at", width: 180, render: formatP6DateTime },
    { title: "操作", key: "actions", fixed: "right", width: 130, render: (_, item) => actions(item) },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/quality")}>← 返回质量驾驶舱</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>合成分歧队列</Typography.Title>
        </div>
        <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
      </Space>

      <P6BoundaryBanner />
      <Alert type="info" showIcon message="确认或豁免只记录人工处置；对应 failed result 保持不可变。" style={{ marginBottom: 16 }} />
      {error && <Alert type="error" showIcon message="分歧队列加载失败" description={error} action={<Button onClick={() => void reload()}>重试</Button>} style={{ marginBottom: 16 }} />}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载分歧队列" /></div>
      ) : (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <div style={{ display: "grid", gridTemplateColumns: screens.sm ? "repeat(3, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))", borderTop: "1px solid #f0f0f0", borderLeft: "1px solid #f0f0f0" }}>
            {([["全部分歧", data.count], ["待处置", data.open_count], ["当前筛选", filtered.length]] as const).map(([label, value]) => (
              <div key={String(label)} style={{ padding: 16, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}><Statistic title={label} value={value} /></div>
            ))}
          </div>

          <Space wrap>
            <Select allowClear placeholder="分歧类型" value={kind} onChange={setKind} style={{ minWidth: 160 }} options={[
              { value: "parser", label: "Parser分歧" }, { value: "ocr", label: "OCR分歧" }, { value: "citation", label: "引用分歧" },
              { value: "refusal", label: "拒答分歧" }, { value: "authorization", label: "权限分歧" }, { value: "injection", label: "注入分歧" },
            ]} />
            <Select allowClear placeholder="处置状态" value={reviewStatus} onChange={setReviewStatus} style={{ minWidth: 150 }} options={[
              { value: "open", label: "待处置" }, { value: "acknowledged", label: "已确认" }, { value: "waived", label: "已豁免" },
            ]} />
          </Space>

          {filtered.length === 0 ? <Empty description={data.count === 0 ? "当前企业没有合成分歧" : "当前筛选没有匹配项"} /> : screens.md ? (
            <Table<QualityDisagreement> rowKey="id" dataSource={filtered} columns={columns} pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 1120 }} />
          ) : (
            <List dataSource={filtered} renderItem={(item) => (
              <List.Item>
                <div style={{ width: "100%" }}>
                  <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Text strong>{disagreementKindCopy(item.kind)}</Typography.Text><Tag color={disagreementReviewColor(item.review_status)}>{disagreementReviewCopy(item.review_status)}</Tag></Space>
                  <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
                    <Descriptions.Item label="分数">{item.score}</Descriptions.Item>
                    <Descriptions.Item label="左侧摘要"><Typography.Text code ellipsis>{item.left_digest}</Typography.Text></Descriptions.Item>
                    <Descriptions.Item label="右侧摘要"><Typography.Text code ellipsis>{item.right_digest}</Typography.Text></Descriptions.Item>
                    <Descriptions.Item label="时间">{formatP6DateTime(item.created_at)}</Descriptions.Item>
                  </Descriptions>
                  {actions(item)}
                </div>
              </List.Item>
            )} />
          )}
        </Space>
      )}

      <DisagreementReviewModal open={Boolean(reviewTarget)} reviewStatus={reviewAction} onCancel={() => setReviewTarget(null)} onSubmit={handleReview} />
    </div>
  );
}
