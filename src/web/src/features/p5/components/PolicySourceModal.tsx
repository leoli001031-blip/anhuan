import { useEffect, useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type {
  CreatePolicySourceInput,
  PolicySource,
  PolicySourceStatus,
  PolicySourceType,
  UpdatePolicySourceInput,
} from "../types";

interface SourceFormValues {
  title: string;
  publisher: string;
  source_type: PolicySourceType;
  jurisdiction: string;
  source_reference: string;
  status: PolicySourceStatus;
}

interface Props {
  open: boolean;
  source?: PolicySource | null;
  onCancel: () => void;
  onSubmit: (input: CreatePolicySourceInput | UpdatePolicySourceInput) => Promise<void>;
}

export default function PolicySourceModal({ open, source, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<SourceFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      title: source?.title ?? "",
      publisher: source?.publisher ?? "",
      source_type: source?.source_type ?? "internal",
      jurisdiction: source?.jurisdiction ?? "",
      source_reference: source?.source_reference ?? "",
      status: source?.status ?? "active",
    });
  }, [form, open, source]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const shared: CreatePolicySourceInput = {
        title: values.title.trim(),
        publisher: values.publisher.trim(),
        source_type: values.source_type,
        jurisdiction: values.jurisdiction.trim(),
        source_reference: values.source_reference.trim(),
      };
      await onSubmit(source ? { ...shared, status: values.status } : shared);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={source ? "编辑政策来源" : "登记政策来源"}
      okText={source ? "保存" : "登记"}
      cancelText="取消"
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        仅登记内部合成或 Fixture 来源元数据；不联网抓取，也不把来源引用处理为外部链接。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="title" label="来源名称" rules={[{ required: true, message: "请输入来源名称" }, { max: 300 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="publisher" label="发布主体" rules={[{ required: true, message: "请输入发布主体" }, { max: 200 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="source_type" label="来源类型" rules={[{ required: true }]}>
          <Select options={[
            { value: "law", label: "法律" },
            { value: "regulation", label: "法规" },
            { value: "standard", label: "标准" },
            { value: "guidance", label: "指导文件" },
            { value: "internal", label: "内部材料" },
          ]} />
        </Form.Item>
        <Form.Item name="jurisdiction" label="地区/层级候选" rules={[{ required: true, message: "请输入地区或层级" }, { max: 120 }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="source_reference" label="内部来源引用" rules={[{ required: true, message: "请输入内部来源引用" }, { max: 500 }]}>
          <Input autoComplete="off" placeholder="仅保存结构化引用，不填写外部 URL" />
        </Form.Item>
        {source && (
          <Form.Item name="status" label="来源状态" rules={[{ required: true }]}>
            <Select options={[
              { value: "active", label: "启用" },
              { value: "archived", label: "归档" },
            ]} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}
