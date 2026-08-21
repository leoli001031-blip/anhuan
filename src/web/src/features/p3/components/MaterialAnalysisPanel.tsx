import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Select, Space, Spin, Table, Tag, Typography, message } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import {
  getMaterialIntakeAnalysis,
  IngestionApiError,
  setMaterialIntakeClassification,
  userFacingIngestionError,
} from "../ingestionApi";
import { reasonCopy } from "../reasonCopy";
import type {
  MaterialClassificationSource,
  MaterialFieldCandidate,
  MaterialIntakeAnalysis,
  MaterialKind,
  MaterialPageClassification,
  VersionSummary,
} from "../types";

interface Props {
  token: string | null;
  version: VersionSummary;
}

const FIELD_COPY: Record<string, string> = {
  source_title: "来源名称",
  publisher: "发布主体",
  source_type: "来源类型",
  jurisdiction: "地区/层级",
  source_reference: "内部来源引用",
  version_title: "版本标题",
  domain: "领域",
  effect_status: "效力状态",
  issued_on: "颁布日期",
  effective_from: "生效起日",
  effective_to: "生效止日",
  summary: "候选摘要",
  report_title: "报告标题",
  report_date: "报告日期",
  report_summary: "报告摘要",
};

function pageKindCopy(kind: string): string {
  if (kind === "text") return "文本型";
  if (kind === "scanned") return "扫描型";
  if (kind === "mixed") return "混合型";
  if (kind === "table") return "表格型";
  if (kind === "two_column") return "双栏型";
  return "待人工判断";
}

function confidenceCopy(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${(Math.max(0, Math.min(1_000_000, value)) / 10_000).toFixed(1)}% 机器线索`;
}

function materialKindCopy(kind: MaterialKind): string {
  if (kind === "policy") return "政策／法规";
  if (kind === "report") return "检测／评估报告";
  return "待分类";
}

function classificationSourceCopy(source: MaterialClassificationSource): string {
  if (source === "human_review") return "人工在分析页确认";
  if (source === "upload_selection") return "上传时人工选择";
  return "机器建议，尚未人工确认";
}

export default function MaterialAnalysisPanel({ token, version }: Props) {
  const navigate = useNavigate();
  const [analysis, setAnalysis] = useState<MaterialIntakeAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<MaterialKind>("unknown");
  const [classificationSaving, setClassificationSaving] = useState(false);
  const [classificationError, setClassificationError] = useState<string | null>(null);
  const activeClassification = useRef<AbortController | null>(null);
  const isPdf = version.content_type === "application/pdf";
  const canRead = isPdf && version.scan_status === "clean" && version.preview_status === "ready";

  useEffect(() => {
    setAnalysis(null);
    setError(null);
    setSelectedKind("unknown");
    setClassificationError(null);
    setClassificationSaving(false);
    activeClassification.current?.abort();
    if (!canRead) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void getMaterialIntakeAnalysis(token, version.id, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setAnalysis(payload);
          setSelectedKind(payload.resolved_kind);
        }
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        if (reason instanceof IngestionApiError && reason.status === 404) {
          setError("机器分析尚未生成；现有安全预览仍可继续使用");
          return;
        }
        setError(userFacingIngestionError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [canRead, token, version.id, version.updated_at]);

  useEffect(
    () => () => {
      activeClassification.current?.abort();
    },
    [],
  );

  const saveClassification = async () => {
    if (!analysis?.allowed_actions.includes("set_material_kind")) return;
    const controller = new AbortController();
    activeClassification.current?.abort();
    activeClassification.current = controller;
    setClassificationSaving(true);
    setClassificationError(null);
    try {
      const payload = await setMaterialIntakeClassification(
        token,
        analysis.id,
        selectedKind,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setAnalysis(payload);
        setSelectedKind(payload.resolved_kind);
        message.success(`已将材料人工归为“${materialKindCopy(payload.resolved_kind)}”`);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setClassificationError(userFacingIngestionError(reason));
    } finally {
      if (activeClassification.current === controller) activeClassification.current = null;
      if (!controller.signal.aborted) setClassificationSaving(false);
    }
  };

  const pageColumns = useMemo<TableColumnsType<MaterialPageClassification>>(
    () => [
      { title: "页码", dataIndex: "page_number", width: 74, fixed: "left" },
      {
        title: "页面类型",
        dataIndex: "primary_kind",
        width: 110,
        render: pageKindCopy,
      },
      {
        title: "OCR",
        dataIndex: "ocr_required",
        width: 110,
        render: (value: boolean) =>
          value ? <Tag color="gold">需要 OCR</Tag> : <Tag>无需 OCR</Tag>,
      },
      {
        title: "版式线索",
        key: "layout",
        width: 190,
        render: (_, page) => (
          <Space size={[4, 4]} wrap>
            {page.table_candidate && (
              <Tag color="blue" title={confidenceCopy(page.table_confidence_ppm)}>表格候选</Tag>
            )}
            {page.two_column_candidate && (
              <Tag color="purple" title={confidenceCopy(page.two_column_confidence_ppm)}>双栏候选</Tag>
            )}
            {!page.table_candidate && !page.two_column_candidate && <Tag>普通版式</Tag>}
          </Space>
        ),
      },
      {
        title: "文本字符",
        dataIndex: "text_character_count",
        width: 100,
      },
      {
        title: "分类线索（未校准）",
        key: "confidence",
        width: 190,
        render: (_, page) =>
          confidenceCopy(
            page.primary_kind === "scanned"
              ? page.scan_confidence_ppm
              : page.text_confidence_ppm,
          ),
      },
    ],
    [],
  );

  const candidateColumns = useMemo<TableColumnsType<MaterialFieldCandidate>>(
    () => [
      {
        title: "候选字段",
        dataIndex: "field_name",
        width: 130,
        fixed: "left",
        render: (value: string) => FIELD_COPY[value] ?? value,
      },
      {
        title: "机器草稿",
        dataIndex: "candidate_value",
        width: 240,
        render: (value: string) => (
          <Typography.Text style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {value}
          </Typography.Text>
        ),
      },
      { title: "页码", dataIndex: "page_number", width: 74 },
      {
        title: "页码证据",
        dataIndex: "evidence_snippet",
        width: 300,
        render: (value: string) => (
          <Typography.Text type="secondary" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {value || "—"}
          </Typography.Text>
        ),
      },
      {
        title: "置信线索（未校准）",
        dataIndex: "confidence_ppm",
        width: 180,
        render: (value: number | null, candidate) => (
          <Space direction="vertical" size={0}>
            <Typography.Text>{confidenceCopy(value)}</Typography.Text>
            <Typography.Text type="secondary">{candidate.confidence_basis} · 未校准</Typography.Text>
          </Space>
        ),
      },
    ],
    [],
  );

  if (!isPdf) return null;

  let content: React.ReactNode;
  if (!canRead) {
    content = (
      <Alert
        type="info"
        showIcon
        message="等待安全处理完成"
        description="只有本地扫描通过且安全预览完成后，才会生成 PDF 机器分析草稿。"
      />
    );
  } else if (loading) {
    content = (
      <div style={{ minHeight: 180, display: "grid", placeItems: "center" }}>
        <Spin tip="正在读取材料分析" />
      </div>
    );
  } else if (error) {
    content = <Alert type="info" showIcon message="材料分析暂不可用" description={error} />;
  } else if (!analysis) {
    content = <Empty description="尚无材料分析" />;
  } else if (analysis.status === "failed") {
    content = (
      <Alert
        type="warning"
        showIcon
        message="机器分析未完成"
        description={reasonCopy(analysis.reason_code)}
      />
    );
  } else {
    const canConfirm = analysis.allowed_actions.includes("confirm_policy_draft");
    const canSetKind = analysis.allowed_actions.includes("set_material_kind");
    const isClientKnowledge = analysis.knowledge_scope.kind === "client";
    content = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Alert
          type={analysis.status === "confirmed" ? "success" : "warning"}
          showIcon
          message={analysis.status === "confirmed" ? "机器草稿已完成人工确认" : "机器草稿，必须人工确认"}
          description="分类和置信值仅是未校准的机器线索，不是准确率、法规结论或自动审核结果。"
        />
        <Space wrap>
          <Tag color="blue">{pageKindCopy(analysis.document_profile)} PDF</Tag>
          <Tag color={isClientKnowledge ? "purple" : "cyan"}>
            {isClientKnowledge
              ? `客户资料 · ${analysis.knowledge_scope.client_display_name ?? "客户档案"}`
              : "环保服务公司资料"}
          </Tag>
          <Tag>解析：{analysis.parser_backend}</Tag>
          <Tag>PDF Inspector：{analysis.shadow_status === "disabled" ? "关闭" : analysis.shadow_status}</Tag>
          <Tag>{analysis.pages.length} 页</Tag>
          <Tag>{analysis.candidates.length} 个字段候选</Tag>
        </Space>

        <Card size="small" title="材料去向（人工最终决定）">
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space size={[20, 8]} wrap>
              <Space direction="vertical" size={0}>
                <Typography.Text type="secondary">机器建议</Typography.Text>
                <Typography.Text strong>{materialKindCopy(analysis.suggested_kind)}</Typography.Text>
                <Typography.Text type="secondary">
                  {confidenceCopy(analysis.suggested_kind_confidence_ppm)}，仅供参考
                </Typography.Text>
              </Space>
              <Space direction="vertical" size={0}>
                <Typography.Text type="secondary">当前人工分类</Typography.Text>
                <Typography.Text strong>{materialKindCopy(analysis.resolved_kind)}</Typography.Text>
                <Typography.Text type="secondary">
                  {classificationSourceCopy(analysis.classification_source)}
                  {analysis.classification_at
                    ? ` · ${new Date(analysis.classification_at).toLocaleString("zh-CN")}`
                    : ""}
                </Typography.Text>
              </Space>
            </Space>

            <Typography.Text type="secondary">
              机器只提出建议，不会替你决定材料进入哪个业务库；人工分类可在后续继续修正。
            </Typography.Text>

            {isClientKnowledge && analysis.resolved_kind === "policy" && (
              <Alert
                type="warning"
                showIcon
                message="客户范围内的政策材料不会创建公司政策草稿"
                description="材料仍归属于当前客户；机器分类和人工材料类型都不会把它移动到环保服务公司的政策库。"
              />
            )}

            {analysis.resolved_kind === "report" ? (
              <Alert
                type="info"
                showIcon
                message="这份材料不会进入政策库"
                description="报告整理入口待接入；原始文件仍留在受控文档库。"
              />
            ) : analysis.resolved_kind === "unknown" ? (
              <Alert
                type="warning"
                showIcon
                message="请先人工选择材料类型"
                description="待分类材料不会创建政策草稿，也不会自动进入其他业务库。"
              />
            ) : isClientKnowledge ? (
              <Alert
                type="info"
                showIcon
                message="已归为客户范围内的政策材料"
                description="客户归属保持不变，不提供公司政策草稿入口。"
              />
            ) : (
              <Alert
                type="info"
                showIcon
                message="已归为政策材料"
                description="完成安全释放并获得服务端授权后，才可进入政策草稿人工确认。"
              />
            )}

            {classificationError && (
              <Alert type="error" showIcon message="人工分类未保存" description={classificationError} />
            )}

            {canSetKind && (
              <Space wrap>
                <Select<MaterialKind>
                  aria-label="选择材料人工分类"
                  value={selectedKind}
                  style={{ width: 200 }}
                  disabled={classificationSaving}
                  options={[
                    { value: "unknown", label: "待分类" },
                    { value: "policy", label: "政策／法规" },
                    { value: "report", label: "检测／评估报告" },
                  ]}
                  onChange={setSelectedKind}
                />
                <Button
                  type="primary"
                  loading={classificationSaving}
                  onClick={() => void saveClassification()}
                >
                  保存人工分类
                </Button>
              </Space>
            )}
          </Space>
        </Card>

        <section aria-labelledby={`material-pages-${analysis.id}`}>
          <Typography.Title id={`material-pages-${analysis.id}`} level={5}>逐页分析线索</Typography.Title>
          <Table<MaterialPageClassification>
            rowKey="page_number"
            size="small"
            pagination={false}
            dataSource={analysis.pages}
            columns={pageColumns}
            scroll={{ x: 840 }}
          />
        </section>

        <section aria-labelledby={`material-fields-${analysis.id}`}>
          <Typography.Title id={`material-fields-${analysis.id}`} level={5}>字段候选与页码证据</Typography.Title>
          {analysis.candidates.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有达到保留条件的字段候选，请人工录入" />
          ) : (
            <Table<MaterialFieldCandidate>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={analysis.candidates}
              columns={candidateColumns}
              scroll={{ x: 930 }}
            />
          )}
        </section>

        <Space wrap>
          {canConfirm && !isClientKnowledge && (
            <Button
              type="primary"
              onClick={() => navigate(`/policies/import/${encodeURIComponent(version.id)}`)}
            >
              人工确认并建立政策草稿
            </Button>
          )}
          {analysis.allowed_actions.includes("view_policy_source") && analysis.policy_source_id && (
            <Button onClick={() => navigate(`/policies/sources/${analysis.policy_source_id}`)}>
              查看政策来源
            </Button>
          )}
          {analysis.allowed_actions.includes("view_policy_version") && analysis.policy_version_id && (
            <Button onClick={() => navigate(`/policies/versions/${analysis.policy_version_id}`)}>
              查看版本草稿
            </Button>
          )}
          {(!canConfirm || isClientKnowledge) && analysis.status !== "confirmed" && (
            <Typography.Text type="secondary">
              {isClientKnowledge && analysis.resolved_kind === "policy"
                ? "客户资料不会进入环保服务公司的政策草稿流程。"
                : "文档解除隔离且服务端授权后，才能进入人工确认。"}
            </Typography.Text>
          )}
        </Space>
      </Space>
    );
  }

  return <Card title="PDF 材料分析与录入候选">{content}</Card>;
}
