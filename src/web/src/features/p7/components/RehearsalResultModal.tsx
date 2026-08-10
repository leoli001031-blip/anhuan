import { useEffect, useState } from "react";
import { Alert, Form, Input, Modal, Select, Typography } from "antd";
import type { RecordRehearsalResultInput, RehearsalCheckResult } from "../types";

interface Props {
  open: boolean;
  result?: RehearsalCheckResult | null;
  onCancel: () => void;
  onSubmit: (input: RecordRehearsalResultInput) => Promise<void>;
}

interface FormValues {
  status: "passed" | "failed" | "blocked";
  evidence_sha256: string;
}

const REASON_BY_STATUS = {
  passed: "MANUAL_CHECK_PASSED",
  failed: "MANUAL_CHECK_FAILED",
  blocked: "MANUAL_CHECK_BLOCKED",
} as const;

export default function RehearsalResultModal({ open, result, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);
  const status = Form.useWatch("status", form) ?? "passed";
  useEffect(() => {
    if (open) form.setFieldsValue({ status: "passed", evidence_sha256: "" });
  }, [form, open, result]);
  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        status: values.status,
        reason_code: REASON_BY_STATUS[values.status],
        evidence_sha256: values.evidence_sha256.trim(),
      });
    } finally { setSaving(false); }
  };
  return (
    <Modal open={open} title="记录人工计划结果" okText="冻结本项结果" cancelText="取消" okButtonProps={{ danger: status !== "passed" }} confirmLoading={saving} onCancel={onCancel} onOk={() => void submit()} afterOpenChange={(visible) => { if (!visible) form.resetFields(); }}>
      <Alert type="warning" showIcon message="结果从 pending 首次记录后即不可修改。失败或阻断的必需项会要求回滚。" style={{ marginBottom: 16 }} />
      <Typography.Paragraph type="secondary">只登记固定原因码与可选 SHA-256，不粘贴命令、日志正文、路径或凭据。</Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="status" label="人工检查结论" rules={[{ required: true }]}><Select options={[
          { value: "passed", label: "通过" }, { value: "failed", label: "失败" }, { value: "blocked", label: "阻断" },
        ]} /></Form.Item>
        <Form.Item label="固定原因码"><Typography.Text code>{REASON_BY_STATUS[status]}</Typography.Text></Form.Item>
        <Form.Item name="evidence_sha256" label="证据SHA-256" rules={[{ required: true, message: "请输入证据SHA-256" }, { pattern: /^[0-9a-f]{64}$/, message: "请输入64位小写SHA-256" }]}><Input autoComplete="off" maxLength={64} /></Form.Item>
      </Form>
    </Modal>
  );
}
