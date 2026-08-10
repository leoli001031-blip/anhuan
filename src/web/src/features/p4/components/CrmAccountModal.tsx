import { useEffect, useState } from "react";
import { Form, Input, Modal, Select, Typography } from "antd";
import type {
  CreateCrmAccountInput,
  CrmAccount,
  CrmStage,
  UpdateCrmAccountInput,
} from "../types";

interface AccountFormValues {
  display_name: string;
  stage: CrmStage;
  owner_user_id?: string;
  industry_note?: string;
  region_note?: string;
  next_follow_up_at?: string;
}

interface Props {
  open: boolean;
  account?: CrmAccount | null;
  onCancel: () => void;
  onSubmit: (input: CreateCrmAccountInput | UpdateCrmAccountInput) => Promise<void>;
}

function localDateTime(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function isoDateTime(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export default function CrmAccountModal({ open, account, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<AccountFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      display_name: account?.display_name ?? "",
      stage: account?.stage ?? "lead",
      owner_user_id: account?.owner_user_id ?? undefined,
      industry_note: account?.industry_note ?? undefined,
      region_note: account?.region_note ?? undefined,
      next_follow_up_at: localDateTime(account?.next_follow_up_at),
    });
  }, [account, form, open]);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({
        display_name: values.display_name.trim(),
        stage: values.stage,
        owner_user_id: values.owner_user_id?.trim() || null,
        industry_note: values.industry_note?.trim() || null,
        region_note: values.region_note?.trim() || null,
        next_follow_up_at: isoDateTime(values.next_follow_up_at),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={account ? "编辑客户档案" : "新建内部客户档案"}
      okText={account ? "保存" : "创建"}
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => void submit()}
      onCancel={onCancel}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        仅录入内部合成或 Fixture 数据，不录入真实客户与联系人信息。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item
          name="display_name"
          label="档案名称"
          rules={[{ required: true, message: "请输入档案名称" }, { max: 200 }]}
        >
          <Input placeholder="例如：合成客户 A" autoComplete="off" />
        </Form.Item>
        <Form.Item name="stage" label="阶段" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "lead", label: "线索" },
              { value: "active", label: "活跃" },
              { value: "dormant", label: "暂缓" },
              { value: "closed", label: "已关闭" },
            ]}
          />
        </Form.Item>
        <Form.Item name="owner_user_id" label="负责人用户 ID">
          <Input placeholder="留空表示暂不指定" autoComplete="off" />
        </Form.Item>
        <Form.Item name="industry_note" label="行业备注">
          <Input.TextArea rows={2} maxLength={2000} showCount />
        </Form.Item>
        <Form.Item name="region_note" label="区域备注">
          <Input.TextArea rows={2} maxLength={2000} showCount />
        </Form.Item>
        <Form.Item name="next_follow_up_at" label="下次跟进时间">
          <Input type="datetime-local" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
