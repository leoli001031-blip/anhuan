import { useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type { CreateReportVersionInput } from "../types";

interface Props {
  open: boolean;
  nextVersionNumber: number;
  onCancel: () => void;
  onSubmit: (input: CreateReportVersionInput) => Promise<void>;
}

export default function ReportVersionModal({
  open,
  nextVersionNumber,
  onCancel,
  onSubmit,
}: Props) {
  const [form] = Form.useForm<CreateReportVersionInput>();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({ change_note: values.change_note?.trim() || null });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={`捕获 v${nextVersionNumber} 业务快照`}
      okText="捕获新版本"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        新版本将基于当前稳定业务事实生成；已有版本保持不可变并转为历史版本。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="change_note" label="版本说明">
          <Input.TextArea rows={4} maxLength={2000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
