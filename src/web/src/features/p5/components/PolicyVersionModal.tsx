import { useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type {
  CreatePolicyVersionInput,
  PolicyDomain,
  PolicyEffectStatus,
} from "../types";

interface Props {
  open: boolean;
  onCancel: () => void;
  onSubmit: (input: CreatePolicyVersionInput) => Promise<void>;
}

export default function PolicyVersionModal({ open, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<CreatePolicyVersionInput>();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        title: values.title.trim(),
        domain: values.domain as PolicyDomain,
        effect_status: values.effect_status as PolicyEffectStatus,
        issued_on: values.issued_on || null,
        effective_from: values.effective_from || null,
        effective_to: values.effective_to || null,
        summary: values.summary.trim(),
        document_version_id: values.document_version_id?.trim() || null,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="建立版本候选"
      okText="保存草稿"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        只保存候选摘要和受控文档 opaque ID；页面不读取或展示文档正文。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false} initialValues={{ domain: "general", effect_status: "unknown" }}>
        <Form.Item name="title" label="版本标题" rules={[{ required: true, message: "请输入版本标题" }, { max: 300 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="domain" label="领域候选" rules={[{ required: true }]}>
          <Select options={[
            { value: "safety", label: "安全" },
            { value: "health", label: "职业健康" },
            { value: "environment", label: "环境" },
            { value: "fire", label: "消防" },
            { value: "chemical", label: "化学品" },
            { value: "general", label: "综合" },
          ]} />
        </Form.Item>
        <Form.Item name="effect_status" label="效力候选" rules={[{ required: true }]}>
          <Select options={[
            { value: "unknown", label: "未知候选" },
            { value: "not_effective", label: "尚未生效候选" },
            { value: "effective", label: "有效候选" },
            { value: "expired", label: "失效候选" },
          ]} />
        </Form.Item>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
          <Form.Item name="issued_on" label="颁布日期"><Input type="date" /></Form.Item>
          <Form.Item name="effective_from" label="生效起日"><Input type="date" /></Form.Item>
          <Form.Item name="effective_to" label="生效止日"><Input type="date" /></Form.Item>
        </div>
        <Form.Item name="summary" label="内部候选摘要" rules={[{ required: true, message: "请输入内部候选摘要" }]}>
          <Input.TextArea rows={4} maxLength={4000} showCount />
        </Form.Item>
        <Form.Item name="document_version_id" label="受控文档版本 ID（可选）">
          <Input autoComplete="off" placeholder="仅允许 ready + released + clean + preview ready" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
