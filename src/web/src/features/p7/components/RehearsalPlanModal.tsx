import { useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type { CreateRehearsalPlanInput } from "../types";

interface Props {
  open: boolean;
  onCancel: () => void;
  onSubmit: (input: CreateRehearsalPlanInput) => Promise<void>;
}

export default function RehearsalPlanModal({ open, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<CreateRehearsalPlanInput>();
  const [saving, setSaving] = useState(false);
  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try { await onSubmit({ name: values.name.trim() }); } finally { setSaving(false); }
  };
  return (
    <Modal open={open} title="创建本地演练计划" okText="创建计划" cancelText="取消" confirmLoading={saving} onCancel={onCancel} onOk={() => void submit()} afterOpenChange={(visible) => { if (!visible) form.resetFields(); }}>
      <Typography.Paragraph type="secondary">计划只编排人工计划与检查清单，不会启动任何基础设施动作。</Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="name" label="计划名称" rules={[{ required: true, message: "请输入计划名称" }, { max: 200 }]}><Input autoComplete="off" maxLength={200} /></Form.Item>
      </Form>
    </Modal>
  );
}
