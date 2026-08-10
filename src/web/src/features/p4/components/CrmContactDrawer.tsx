import { useEffect, useState } from "react";
import { Button, Drawer, Form, Input, Select, Space, Typography } from "antd";
import type {
  ContactStatus,
  CreateCrmContactInput,
  CrmContact,
  UpdateCrmContactInput,
} from "../types";

interface ContactFormValues {
  display_name: string;
  role_title?: string;
  email?: string;
  phone?: string;
  status: ContactStatus;
}

interface Props {
  open: boolean;
  contact?: CrmContact | null;
  onCancel: () => void;
  onSubmit: (input: CreateCrmContactInput | UpdateCrmContactInput) => Promise<void>;
}

export default function CrmContactDrawer({ open, contact, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<ContactFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      display_name: contact?.display_name ?? "",
      role_title: contact?.role_title ?? undefined,
      email: contact?.email ?? undefined,
      phone: contact?.phone ?? undefined,
      status: contact?.status ?? "active",
    });
  }, [contact, form, open]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        display_name: values.display_name.trim(),
        role_title: values.role_title?.trim() || null,
        email: values.email?.trim() || null,
        phone: values.phone?.trim() || null,
        status: values.status,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      open={open}
      width="min(560px, 100vw)"
      title={contact ? "编辑联系人" : "新增联系人"}
      onClose={onCancel}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
      extra={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={saving} onClick={() => void submit()}>
            保存
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">
        联系方式仅在当前授权页面显示，不会写入日志、时间线或浏览器存储。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item
          name="display_name"
          label="姓名"
          rules={[{ required: true, message: "请输入联系人姓名" }, { max: 200 }]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="role_title" label="职务">
          <Input maxLength={200} autoComplete="off" />
        </Form.Item>
        <Form.Item name="email" label="邮箱" rules={[{ type: "email", message: "邮箱格式不正确" }]}>
          <Input maxLength={320} autoComplete="off" />
        </Form.Item>
        <Form.Item name="phone" label="电话">
          <Input maxLength={64} autoComplete="off" />
        </Form.Item>
        <Form.Item name="status" label="状态" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "active", label: "有效" },
              { value: "inactive", label: "停用" },
            ]}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
