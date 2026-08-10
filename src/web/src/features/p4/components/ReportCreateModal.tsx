import { useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type { CreateBusinessReportInput } from "../types";

interface Props {
  open: boolean;
  onCancel: () => void;
  onSubmit: (input: CreateBusinessReportInput) => Promise<void>;
}

export default function ReportCreateModal({ open, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<CreateBusinessReportInput>();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        service_case_id: values.service_case_id.trim(),
        title: values.title.trim(),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="建立业务报告快照"
      okText="创建并捕获首版"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        系统仅捕获当前可见业务事实的 canonical JSON 快照，不生成 PDF、HTML 或专业结论。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item
          name="service_case_id"
          label="服务任务 ID"
          rules={[{ required: true, message: "请输入服务任务 ID" }]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="title"
          label="报告标题"
          rules={[{ required: true, message: "请输入报告标题" }, { max: 200 }]}
        >
          <Input autoComplete="off" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
