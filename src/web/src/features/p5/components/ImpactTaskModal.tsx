import { useEffect, useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type {
  CreateImpactTaskInput,
  PolicyImpactTask,
  UpdateImpactTaskInput,
} from "../types";

interface TaskFormValues {
  title: string;
  owner_user_id: string;
  due_at: string;
}

interface Props {
  open: boolean;
  task?: PolicyImpactTask | null;
  onCancel: () => void;
  onSubmit: (input: CreateImpactTaskInput | UpdateImpactTaskInput) => Promise<void>;
}

function localDateTime(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function toIso(value: string): string {
  return new Date(value).toISOString();
}

export default function ImpactTaskModal({ open, task, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<TaskFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      title: task?.title ?? "",
      owner_user_id: task?.owner_user_id ?? "",
      due_at: localDateTime(task?.due_at),
    });
  }, [form, open, task]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        title: values.title.trim(),
        owner_user_id: values.owner_user_id.trim(),
        due_at: toIso(values.due_at),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={task ? "编辑影响待办" : "建立影响待办"}
      okText={task ? "保存" : "建立待办"}
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        待办只在站内流转，不触发短信、邮件、微信或外部工单。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="title" label="待办标题" rules={[{ required: true, message: "请输入待办标题" }, { max: 300 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="owner_user_id" label="负责人用户 ID" rules={[{ required: true, message: "请输入同企业负责人 ID" }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="due_at" label="截止时间" rules={[{ required: true, message: "请选择截止时间" }]}>
          <Input type="datetime-local" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
