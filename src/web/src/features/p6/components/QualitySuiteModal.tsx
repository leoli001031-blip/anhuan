import { useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type { CreateQualitySuiteInput } from "../types";

interface Props {
  open: boolean;
  onCancel: () => void;
  onSubmit: (input: CreateQualitySuiteInput) => Promise<void>;
}

export default function QualitySuiteModal({ open, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<CreateQualitySuiteInput>();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({ name: values.name.trim(), category: values.category });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="创建质量套件"
      okText="创建"
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        套件仅组织合成场景和本地确定性 Oracle，不接入真实客户输入或外部模型。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false} initialValues={{ category: "qa" }}>
        <Form.Item name="name" label="套件名称" rules={[{ required: true, message: "请输入套件名称" }, { max: 200 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="category" label="质量领域" rules={[{ required: true }]}>
          <Select options={[
            { value: "ingestion", label: "受控导入" },
            { value: "retrieval", label: "检索" },
            { value: "qa", label: "问答" },
            { value: "authorization", label: "权限隔离" },
            { value: "injection", label: "注入防护" },
          ]} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
