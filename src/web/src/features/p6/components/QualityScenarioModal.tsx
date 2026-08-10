import { useEffect, useMemo, useState } from "react";
import { Form, Input, InputNumber, Modal, Select, Switch, Typography } from "antd";
import type {
  CreateQualityScenarioInput,
  DisagreementKind,
  QualityScenario,
  ScenarioSeverity,
  ScenarioType,
  UpdateQualityScenarioInput,
} from "../types";

interface ScenarioFormValues {
  scenario_key: string;
  scenario_type: ScenarioType;
  severity: ScenarioSeverity;
  enabled: boolean;
  disagreement_kind?: DisagreementKind;
  expected_sha256?: string;
  actual_sha256?: string;
  min_value?: number;
  max_value?: number;
  value?: number;
  expected_reason?: string;
  refused?: boolean;
  refusal_reason?: string;
  unsafe_action_executed?: boolean;
  mode?: string;
  outcome?: string;
  visible_rows?: number;
  blocked?: boolean;
  privileged_action_executed?: boolean;
  external_call_count?: number;
  max_score?: number;
  left_sha256?: string;
  right_sha256?: string;
  score?: number;
}

interface Props {
  open: boolean;
  scenario?: QualityScenario | null;
  onCancel: () => void;
  onSubmit: (input: CreateQualityScenarioInput | UpdateQualityScenarioInput) => Promise<void>;
}

const DIGEST_RULE = { pattern: /^[0-9a-f]{64}$/, message: "请输入64位小写SHA-256" };
const DISAGREEMENT_OPTIONS = [
  { value: "parser", label: "Parser" },
  { value: "ocr", label: "OCR" },
  { value: "citation", label: "引用" },
  { value: "refusal", label: "拒答" },
  { value: "authorization", label: "权限" },
  { value: "injection", label: "注入" },
];

function metric<T>(value: unknown, fallback: T): T {
  return (value === undefined || value === null ? fallback : value) as T;
}

export default function QualityScenarioModal({ open, scenario, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<ScenarioFormValues>();
  const [saving, setSaving] = useState(false);
  const scenarioType = Form.useWatch("scenario_type", form) ?? scenario?.scenario_type ?? "exact_match";

  useEffect(() => {
    if (!open) return;
    const config = scenario?.oracle_config ?? {};
    const observation = scenario?.synthetic_observation ?? {};
    form.setFieldsValue({
      scenario_key: scenario?.scenario_key ?? "",
      scenario_type: scenario?.scenario_type ?? "exact_match",
      severity: scenario?.severity ?? "medium",
      enabled: scenario?.enabled ?? true,
      disagreement_kind: metric(config.disagreement_kind, undefined),
      expected_sha256: metric(config.expected_sha256, ""),
      actual_sha256: metric(observation.actual_sha256, ""),
      min_value: metric(config.min_value, undefined),
      max_value: metric(config.max_value, undefined),
      value: metric(observation.value, undefined),
      expected_reason: metric(config.expected_reason, "policy_guard"),
      refused: metric(observation.refused, true),
      refusal_reason: metric(observation.refusal_reason, "policy_guard"),
      unsafe_action_executed: metric(observation.unsafe_action_executed, false),
      mode: metric(config.mode, "collection_zero_rows"),
      outcome: metric(observation.outcome, "ok"),
      visible_rows: metric(observation.visible_rows, 0),
      blocked: metric(observation.blocked, true),
      privileged_action_executed: metric(observation.privileged_action_executed, false),
      external_call_count: metric(observation.external_call_count, 0),
      max_score: metric(config.max_score, 0),
      left_sha256: metric(observation.left_sha256, ""),
      right_sha256: metric(observation.right_sha256, ""),
      score: metric(observation.score, 0),
    });
  }, [form, open, scenario]);

  const payloads = (values: ScenarioFormValues) => {
    const config: Record<string, number | boolean | string> = { schema_version: 1 };
    const observation: Record<string, number | boolean | string> = { schema_version: 1 };
    if (values.scenario_type === "exact_match") {
      config.expected_sha256 = values.expected_sha256 ?? "";
      observation.actual_sha256 = values.actual_sha256 ?? "";
    } else if (values.scenario_type === "threshold") {
      if (values.min_value !== undefined) config.min_value = values.min_value;
      if (values.max_value !== undefined) config.max_value = values.max_value;
      observation.value = values.value ?? 0;
    } else if (values.scenario_type === "refusal_required") {
      config.expected_reason = values.expected_reason ?? "policy_guard";
      observation.refused = values.refused ?? false;
      observation.refusal_reason = values.refusal_reason ?? "none";
      observation.unsafe_action_executed = values.unsafe_action_executed ?? false;
    } else if (values.scenario_type === "isolation_required") {
      config.mode = values.mode ?? "collection_zero_rows";
      observation.outcome = values.outcome ?? "error";
      observation.visible_rows = values.visible_rows ?? 0;
    } else if (values.scenario_type === "injection_blocked") {
      config.guard_mode = "block";
      observation.blocked = values.blocked ?? false;
      observation.privileged_action_executed = values.privileged_action_executed ?? false;
      observation.external_call_count = values.external_call_count ?? 0;
    } else {
      config.max_score = values.max_score ?? 0;
      observation.left_sha256 = values.left_sha256 ?? "";
      observation.right_sha256 = values.right_sha256 ?? "";
      observation.score = values.score ?? 0;
    }
    if (values.disagreement_kind) config.disagreement_kind = values.disagreement_kind;
    return { oracle_config: config, synthetic_observation: observation };
  };

  const submit = async () => {
    const values = await form.validateFields();
    if (values.scenario_type === "threshold" && values.min_value === undefined && values.max_value === undefined) {
      form.setFields([{ name: "min_value", errors: ["最小值和最大值至少填写一个"] }]);
      return;
    }
    const payload = payloads(values);
    setSaving(true);
    try {
      await onSubmit(
        scenario
          ? {
              severity: values.severity,
              enabled: values.enabled,
              ...payload,
            }
          : {
              scenario_key: values.scenario_key.trim(),
              scenario_type: values.scenario_type,
              severity: values.severity,
              enabled: values.enabled,
              ...payload,
            },
      );
    } finally {
      setSaving(false);
    }
  };

  const optionalKind = useMemo(() => {
    if (scenarioType === "refusal_required") return [{ value: "refusal", label: "拒答" }];
    if (scenarioType === "isolation_required") return [{ value: "authorization", label: "权限" }];
    if (scenarioType === "injection_blocked") return [{ value: "injection", label: "注入" }];
    if (scenarioType === "disagreement_max") return DISAGREEMENT_OPTIONS;
    return DISAGREEMENT_OPTIONS.slice(0, 3);
  }, [scenarioType]);

  useEffect(() => {
    const current = form.getFieldValue("disagreement_kind");
    if (current && !optionalKind.some((option) => option.value === current)) {
      form.setFieldValue("disagreement_kind", undefined);
    }
  }, [form, optionalKind, scenarioType]);

  return (
    <Modal
      open={open}
      width="min(720px, 100vw)"
      title={scenario ? "编辑合成场景" : "登记合成场景"}
      okText={scenario ? "保存" : "登记"}
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        表单只生成固定字段、数字、布尔、枚举与SHA；不会保存原文、问题、文件名或外部响应。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
          <Form.Item name="scenario_key" label="场景键" rules={[{ required: true, message: "请输入场景键" }, { pattern: /^[a-z0-9][a-z0-9._-]{0,79}$/, message: "最多80位，仅允许小写字母、数字、点、_和-" }]}>
            <Input disabled={Boolean(scenario)} autoComplete="off" />
          </Form.Item>
          <Form.Item name="scenario_type" label="Oracle类型" rules={[{ required: true }]}>
            <Select disabled={Boolean(scenario)} options={[
              { value: "exact_match", label: "精确SHA匹配" },
              { value: "threshold", label: "数值阈值" },
              { value: "refusal_required", label: "必须拒答" },
              { value: "isolation_required", label: "必须隔离" },
              { value: "injection_blocked", label: "必须阻断注入" },
              { value: "disagreement_max", label: "分歧上限" },
            ]} />
          </Form.Item>
          <Form.Item name="severity" label="严重程度" rules={[{ required: true }]}>
            <Select options={[
              { value: "low", label: "低" }, { value: "medium", label: "中" }, { value: "high", label: "高" }, { value: "critical", label: "严重" },
            ]} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </div>

        {scenarioType === "exact_match" && (
          <>
            <Form.Item name="expected_sha256" label="期望SHA-256" rules={[{ required: true }, DIGEST_RULE]}><Input autoComplete="off" /></Form.Item>
            <Form.Item name="actual_sha256" label="合成观察SHA-256" rules={[{ required: true }, DIGEST_RULE]}><Input autoComplete="off" /></Form.Item>
          </>
        )}
        {scenarioType === "threshold" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
            <Form.Item name="min_value" label="最小值"><InputNumber style={{ width: "100%" }} /></Form.Item>
            <Form.Item name="max_value" label="最大值"><InputNumber style={{ width: "100%" }} /></Form.Item>
            <Form.Item name="value" label="合成观察值" rules={[{ required: true }]}><InputNumber style={{ width: "100%" }} /></Form.Item>
          </div>
        )}
        {scenarioType === "refusal_required" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
            <Form.Item name="expected_reason" label="期望拒答原因" rules={[{ required: true }]}><Select options={[
              { value: "policy_guard", label: "政策闸门" }, { value: "authorization_guard", label: "权限闸门" }, { value: "unsupported_request", label: "不支持请求" }, { value: "injection_guard", label: "注入闸门" },
            ]} /></Form.Item>
            <Form.Item name="refusal_reason" label="观察拒答原因" rules={[{ required: true }]}><Select options={[
              { value: "none", label: "无" }, { value: "policy_guard", label: "政策闸门" }, { value: "authorization_guard", label: "权限闸门" }, { value: "unsupported_request", label: "不支持请求" }, { value: "injection_guard", label: "注入闸门" },
            ]} /></Form.Item>
            <Form.Item name="refused" label="已拒答" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="unsafe_action_executed" label="执行危险动作" valuePropName="checked"><Switch /></Form.Item>
          </div>
        )}
        {scenarioType === "isolation_required" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
            <Form.Item name="mode" label="隔离模式" rules={[{ required: true }]}><Select options={[
              { value: "collection_zero_rows", label: "集合零行" }, { value: "detail_not_found", label: "详情404" },
            ]} /></Form.Item>
            <Form.Item name="outcome" label="观察结果" rules={[{ required: true }]}><Select options={[
              { value: "ok", label: "OK" }, { value: "not_found", label: "Not Found" }, { value: "forbidden", label: "Forbidden" }, { value: "error", label: "Error" },
            ]} /></Form.Item>
            <Form.Item name="visible_rows" label="可见跨租户行" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: "100%" }} /></Form.Item>
          </div>
        )}
        {scenarioType === "injection_blocked" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
            <Form.Item name="blocked" label="已阻断" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="privileged_action_executed" label="执行特权动作" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="external_call_count" label="外部调用数" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: "100%" }} /></Form.Item>
          </div>
        )}
        {scenarioType === "disagreement_max" && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
              <Form.Item name="max_score" label="最大分歧分数" rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} /></Form.Item>
              <Form.Item name="score" label="观察分歧分数" rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} /></Form.Item>
            </div>
            <Form.Item name="left_sha256" label="左侧SHA-256" rules={[{ required: true }, DIGEST_RULE]}><Input autoComplete="off" /></Form.Item>
            <Form.Item name="right_sha256" label="右侧SHA-256" rules={[{ required: true }, DIGEST_RULE]}><Input autoComplete="off" /></Form.Item>
          </>
        )}
        <Form.Item name="disagreement_kind" label="失败时生成分歧类型" rules={scenarioType === "disagreement_max" ? [{ required: true, message: "请选择分歧类型" }] : []}>
          <Select allowClear={scenarioType !== "disagreement_max"} options={optionalKind} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
