import { useEffect, useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type {
  CreatePolicyImpactInput,
  ImpactPriority,
  PolicyDomain,
  PolicyImpact,
  UpdatePolicyImpactInput,
} from "../types";

interface ImpactFormValues {
  policy_version_id: string;
  domain: PolicyDomain;
  scope_note: string;
  priority: ImpactPriority;
}

interface Props {
  open: boolean;
  impact?: PolicyImpact | null;
  initialVersionId?: string;
  initialDomain?: PolicyDomain;
  onCancel: () => void;
  onSubmit: (input: CreatePolicyImpactInput | UpdatePolicyImpactInput) => Promise<void>;
}

export default function PolicyImpactModal({
  open,
  impact,
  initialVersionId,
  initialDomain,
  onCancel,
  onSubmit,
}: Props) {
  const [form] = Form.useForm<ImpactFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      policy_version_id: impact?.policy_version_id ?? initialVersionId ?? "",
      domain: impact?.domain ?? initialDomain ?? "general",
      scope_note: impact?.scope_note ?? "",
      priority: impact?.priority ?? "medium",
    });
  }, [form, impact, initialDomain, initialVersionId, open]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (impact) {
        await onSubmit({
          scope_note: values.scope_note.trim(),
          priority: values.priority,
        });
      } else {
        await onSubmit({
          policy_version_id: values.policy_version_id.trim(),
          domain: values.domain,
          scope_note: values.scope_note.trim(),
          priority: values.priority,
        });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={impact ? "编辑影响候选" : "建立影响候选"}
      okText={impact ? "保存" : "建立候选"}
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        影响范围由人工登记，仅作为待研判候选，不声明法规适用。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="policy_version_id" label="政策版本 ID" rules={[{ required: true, message: "请输入政策版本 ID" }]}>
          <Input disabled={Boolean(impact || initialVersionId)} autoComplete="off" />
        </Form.Item>
        <Form.Item name="domain" label="领域候选" rules={[{ required: true }]}>
          <Select disabled={Boolean(impact)} options={[
            { value: "safety", label: "安全" },
            { value: "health", label: "职业健康" },
            { value: "environment", label: "环境" },
            { value: "fire", label: "消防" },
            { value: "chemical", label: "化学品" },
            { value: "general", label: "综合" },
          ]} />
        </Form.Item>
        <Form.Item name="priority" label="研判优先级" rules={[{ required: true }]}>
          <Select options={[
            { value: "low", label: "低" },
            { value: "medium", label: "中" },
            { value: "high", label: "高" },
            { value: "critical", label: "紧急" },
          ]} />
        </Form.Item>
        <Form.Item name="scope_note" label="候选范围说明" rules={[{ required: true, message: "请输入候选范围说明" }, { max: 4000 }]}>
          <Input.TextArea rows={5} maxLength={4000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
