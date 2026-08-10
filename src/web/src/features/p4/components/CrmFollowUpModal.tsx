import { useEffect, useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type { CreateCrmFollowUpInput, FollowUpChannel } from "../types";

interface FollowUpFormValues {
  channel: FollowUpChannel;
  summary: string;
  next_action?: string;
  next_due_at?: string;
  occurred_at: string;
}

interface Props {
  open: boolean;
  onCancel: () => void;
  onSubmit: (input: CreateCrmFollowUpInput) => Promise<void>;
}

function currentLocalDateTime(): string {
  const date = new Date();
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function toIso(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export default function CrmFollowUpModal({ open, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<FollowUpFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) form.setFieldsValue({ channel: "internal_note", occurred_at: currentLocalDateTime() });
  }, [form, open]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        channel: values.channel,
        summary: values.summary.trim(),
        next_action: values.next_action?.trim() || null,
        next_due_at: toIso(values.next_due_at),
        occurred_at: toIso(values.occurred_at) ?? new Date().toISOString(),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="登记人工跟进"
      okText="登记"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        跟进记录写入后不可修改或删除，请确认摘要准确且不含真实敏感信息。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="channel" label="渠道" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "onsite", label: "现场" },
              { value: "meeting", label: "会议" },
              { value: "phone", label: "电话" },
              { value: "internal_note", label: "内部记录" },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="summary"
          label="跟进摘要"
          rules={[{ required: true, message: "请输入跟进摘要" }, { max: 4000 }]}
        >
          <Input.TextArea rows={4} showCount maxLength={4000} />
        </Form.Item>
        <Form.Item name="occurred_at" label="发生时间" rules={[{ required: true }]}>
          <Input type="datetime-local" />
        </Form.Item>
        <Form.Item name="next_action" label="下一步">
          <Input.TextArea rows={2} showCount maxLength={2000} />
        </Form.Item>
        <Form.Item name="next_due_at" label="下一次到期时间">
          <Input type="datetime-local" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
