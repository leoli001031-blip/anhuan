import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Select,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { getMaterialIntakeAnalysis, IngestionApiError } from "../../p3/ingestionApi";
import type { MaterialFieldCandidate, MaterialIntakeAnalysis } from "../../p3/types";
import P5BoundaryBanner from "../components/P5BoundaryBanner";
import { useP5TenantQuery } from "../hooks/useP5TenantQuery";
import {
  confirmMaterialPolicyDraft,
  isPolicyWorkflowRequestAborted,
  PolicyWorkflowApiError,
  userFacingPolicyWorkflowError,
} from "../policyWorkflowApi";
import type {
  ConfirmMaterialPolicyDraftInput,
  PolicyDomain,
  PolicyEffectStatus,
  PolicySourceType,
} from "../types";

const SOURCE_TYPES: PolicySourceType[] = ["law", "regulation", "standard", "guidance", "internal"];
const DOMAINS: PolicyDomain[] = ["safety", "health", "environment", "fire", "chemical", "general"];
const EFFECT_STATUSES: PolicyEffectStatus[] = ["unknown", "not_effective", "effective", "expired"];

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function confidenceCopy(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "未提供置信线索";
  return `${(Math.max(0, Math.min(1_000_000, value)) / 10_000).toFixed(1)}% 机器线索`;
}

function CandidateEvidence({ candidate }: { candidate: MaterialFieldCandidate | undefined }) {
  if (!candidate) return <Typography.Text type="secondary">无机器候选，请人工填写</Typography.Text>;
  return (
    <Space direction="vertical" size={2} style={{ width: "100%" }}>
      <Typography.Text type="secondary">
        第 {candidate.page_number} 页 · {confidenceCopy(candidate.confidence_ppm)} · 未校准
      </Typography.Text>
      <Typography.Text type="secondary" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        证据：{candidate.evidence_snippet || "—"}
      </Typography.Text>
    </Space>
  );
}

function enumValue<T extends string>(value: string | undefined, allowed: T[], fallback: T): T {
  return value && allowed.includes(value as T) ? (value as T) : fallback;
}

export default function PolicyDraftFromDocumentPage() {
  const { documentVersionId = "" } = useParams<{ documentVersionId: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm<ConfirmMaterialPolicyDraftInput>();
  const [saving, setSaving] = useState(false);
  const idempotencyKey = useRef(newIdempotencyKey());
  const load = useCallback(
    async (token: string | null, signal: AbortSignal) => {
      try {
        return await getMaterialIntakeAnalysis(token, documentVersionId, signal);
      } catch (reason) {
        if (reason instanceof IngestionApiError) {
          throw new PolicyWorkflowApiError(
            reason.status,
            reason.status === 404 ? "NOT_FOUND" : reason.code,
            reason.retryable,
          );
        }
        throw reason;
      }
    },
    [documentVersionId],
  );
  const {
    data: analysis,
    loading,
    error,
    setError,
    reload,
    runMutation,
    tenantEpoch,
  } = useP5TenantQuery<MaterialIntakeAnalysis | null>(null, load);

  const candidateByName = useCallback(
    (fieldName: string): MaterialFieldCandidate | undefined =>
      analysis?.candidates.find(
        (candidate) => candidate.field_name === fieldName && candidate.candidate_value.trim(),
      ),
    [analysis],
  );

  useEffect(() => {
    idempotencyKey.current = newIdempotencyKey();
    setSaving(false);
    form.resetFields();
  }, [form, tenantEpoch]);

  useEffect(() => {
    if (!analysis) return;
    const sourceTitle = candidateByName("source_title")?.candidate_value.trim() ?? "";
    const versionTitle = candidateByName("version_title")?.candidate_value.trim() ?? "";
    form.setFieldsValue({
      source: {
        title: sourceTitle || versionTitle,
        publisher: candidateByName("publisher")?.candidate_value.trim() ?? "",
        source_type: enumValue(
          candidateByName("source_type")?.candidate_value.trim(),
          SOURCE_TYPES,
          "internal",
        ),
        jurisdiction: candidateByName("jurisdiction")?.candidate_value.trim() ?? "",
        source_reference:
          candidateByName("source_reference")?.candidate_value.trim() ||
          `controlled-document-version:${documentVersionId}`,
      },
      version: {
        title: versionTitle || sourceTitle,
        domain: enumValue(
          candidateByName("domain")?.candidate_value.trim(),
          DOMAINS,
          "general",
        ),
        effect_status: enumValue(
          candidateByName("effect_status")?.candidate_value.trim(),
          EFFECT_STATUSES,
          "unknown",
        ),
        issued_on: candidateByName("issued_on")?.candidate_value.trim() || null,
        effective_from: candidateByName("effective_from")?.candidate_value.trim() || null,
        effective_to: candidateByName("effective_to")?.candidate_value.trim() || null,
        summary: candidateByName("summary")?.candidate_value.trim() ?? "",
      },
    });
  }, [analysis, candidateByName, documentVersionId, form]);

  const submit = async () => {
    if (!analysis?.allowed_actions.includes("confirm_policy_draft")) return;
    const values = await form.validateFields();
    const input: ConfirmMaterialPolicyDraftInput = {
      source: {
        title: values.source.title.trim(),
        publisher: values.source.publisher.trim(),
        source_type: values.source.source_type,
        jurisdiction: values.source.jurisdiction.trim(),
        source_reference: values.source.source_reference.trim(),
      },
      version: {
        title: values.version.title.trim(),
        domain: values.version.domain,
        effect_status: values.version.effect_status,
        issued_on: values.version.issued_on || null,
        effective_from: values.version.effective_from || null,
        effective_to: values.version.effective_to || null,
        summary: values.version.summary.trim(),
      },
    };
    setSaving(true);
    setError(null);
    try {
      const result = await runMutation((token, signal) =>
        confirmMaterialPolicyDraft(
          token,
          analysis.id,
          input,
          idempotencyKey.current,
          signal,
        ),
      );
      idempotencyKey.current = newIdempotencyKey();
      message.success("政策来源与版本草稿已创建");
      navigate(`/policies/versions/${result.version.id}`);
    } catch (reason) {
      if (!isPolicyWorkflowRequestAborted(reason)) {
        setError(userFacingPolicyWorkflowError(reason));
      }
    } finally {
      setSaving(false);
    }
  };

  const canConfirm = analysis?.allowed_actions.includes("confirm_policy_draft") ?? false;

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate(-1)}>
            ← 返回受控文档
          </Button>
          <Typography.Title level={3} style={{ margin: 0 }}>人工确认政策草稿</Typography.Title>
          <Typography.Text type="secondary">
            机器候选只负责减少录入；最终字段由你编辑确认
          </Typography.Text>
        </div>
        <Button onClick={() => void reload()} disabled={loading || saving}>刷新分析</Button>
      </Space>

      <P5BoundaryBanner />

      {error && (
        <Alert
          type="error"
          showIcon
          message="材料确认操作未完成"
          description={error}
          action={<Button onClick={() => void reload()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}>
          <Spin tip="正在读取机器候选" />
        </div>
      ) : !analysis && !error ? (
        <Alert type="info" showIcon message="当前文档版本没有可确认的材料分析" />
      ) : analysis?.status === "failed" ? (
        <Alert type="warning" showIcon message="材料分析失败，不能创建政策草稿" description={analysis.reason_code ?? undefined} />
      ) : analysis?.status === "confirmed" ? (
        <Alert
          type="success"
          showIcon
          message="该机器草稿已经完成人工确认"
          action={analysis.policy_version_id ? (
            <Button type="primary" onClick={() => navigate(`/policies/versions/${analysis.policy_version_id}`)}>
              查看版本草稿
            </Button>
          ) : undefined}
        />
      ) : analysis && !canConfirm ? (
        <Alert
          type="info"
          showIcon
          message="当前还不能确认政策草稿"
          description="文档必须完成安全处理并由人工解除隔离，且当前身份需要获得服务端 confirm_policy_draft 权限。"
        />
      ) : analysis ? (
        <>
          <Alert
            type="warning"
            showIcon
            message="所有候选均为未校准机器草稿"
            description="请逐项核对页码证据并修改；本操作只创建 draft，不会自动提交审核、内部发布或形成法律结论。"
            style={{ marginBottom: 16 }}
          />
          <Form form={form} layout="vertical" requiredMark={false}>
            <Card title="政策来源" style={{ marginBottom: 16 }}>
              <Form.Item
                name={["source", "title"]}
                label="来源名称"
                extra={<CandidateEvidence candidate={candidateByName("source_title")} />}
                rules={[{ required: true, whitespace: true, message: "请输入来源名称" }, { max: 300 }]}
              >
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item
                name={["source", "publisher"]}
                label="发布主体"
                extra={<CandidateEvidence candidate={candidateByName("publisher")} />}
                rules={[{ required: true, whitespace: true, message: "请输入发布主体" }, { max: 200 }]}
              >
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item
                name={["source", "source_type"]}
                label="来源类型"
                extra={<CandidateEvidence candidate={candidateByName("source_type")} />}
                rules={[{ required: true }]}
              >
                <Select options={[
                  { value: "law", label: "法律" },
                  { value: "regulation", label: "法规" },
                  { value: "standard", label: "标准" },
                  { value: "guidance", label: "指导文件" },
                  { value: "internal", label: "内部材料" },
                ]} />
              </Form.Item>
              <Form.Item
                name={["source", "jurisdiction"]}
                label="地区/层级"
                extra={<CandidateEvidence candidate={candidateByName("jurisdiction")} />}
                rules={[{ required: true, whitespace: true, message: "请输入地区或层级" }, { max: 120 }]}
              >
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item
                name={["source", "source_reference"]}
                label="内部来源引用"
                extra={<CandidateEvidence candidate={candidateByName("source_reference")} />}
                rules={[{ required: true, whitespace: true, message: "请输入内部来源引用" }, { max: 500 }]}
              >
                <Input autoComplete="off" />
              </Form.Item>
            </Card>

            <Card title="版本草稿">
              <Form.Item
                name={["version", "title"]}
                label="版本标题"
                extra={<CandidateEvidence candidate={candidateByName("version_title")} />}
                rules={[{ required: true, whitespace: true, message: "请输入版本标题" }, { max: 300 }]}
              >
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item
                name={["version", "domain"]}
                label="领域"
                extra={<CandidateEvidence candidate={candidateByName("domain")} />}
                rules={[{ required: true }]}
              >
                <Select options={[
                  { value: "safety", label: "安全" },
                  { value: "health", label: "职业健康" },
                  { value: "environment", label: "环境" },
                  { value: "fire", label: "消防" },
                  { value: "chemical", label: "化学品" },
                  { value: "general", label: "综合" },
                ]} />
              </Form.Item>
              <Form.Item
                name={["version", "effect_status"]}
                label="效力状态"
                extra={<CandidateEvidence candidate={candidateByName("effect_status")} />}
                rules={[{ required: true }]}
              >
                <Select options={[
                  { value: "unknown", label: "未知候选" },
                  { value: "not_effective", label: "尚未生效候选" },
                  { value: "effective", label: "有效候选" },
                  { value: "expired", label: "失效候选" },
                ]} />
              </Form.Item>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                <Form.Item name={["version", "issued_on"]} label="颁布日期" extra={<CandidateEvidence candidate={candidateByName("issued_on")} />}>
                  <Input type="date" />
                </Form.Item>
                <Form.Item name={["version", "effective_from"]} label="生效起日" extra={<CandidateEvidence candidate={candidateByName("effective_from")} />}>
                  <Input type="date" />
                </Form.Item>
                <Form.Item name={["version", "effective_to"]} label="生效止日" extra={<CandidateEvidence candidate={candidateByName("effective_to")} />}>
                  <Input type="date" />
                </Form.Item>
              </div>
              <Form.Item
                name={["version", "summary"]}
                label="内部候选摘要"
                extra={<CandidateEvidence candidate={candidateByName("summary")} />}
                rules={[{ required: true, whitespace: true, message: "请输入候选摘要" }, { max: 4000 }]}
              >
                <Input.TextArea rows={6} maxLength={4000} showCount />
              </Form.Item>
            </Card>

            <Space style={{ marginTop: 16 }}>
              <Button type="primary" loading={saving} onClick={() => void submit()}>
                确认并创建来源与版本草稿
              </Button>
              <Button disabled={saving} onClick={() => navigate(-1)}>取消</Button>
            </Space>
          </Form>
        </>
      ) : null}
    </div>
  );
}
