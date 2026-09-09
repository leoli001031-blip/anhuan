import { useEffect, useRef, useState } from "react";
import { Alert, Form, Input, Modal, Select, Typography } from "antd";
import { useAsyncContext } from "../../../components/useAsyncContext";
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
  contextKey?: string;
  errorMessage?: string | null;
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

export default function CrmAccountModal({ open, account, onCancel, onSubmit, contextKey, errorMessage }: Props) {
  const [form] = Form.useForm<AccountFormValues>();
  const initializedCreate = useRef<string | null>(null);
  const [saving, setSaving] = useState(false);
  const context = JSON.stringify([contextKey, account?.id, open]);
  const current = useAsyncContext(context);
  const flight = useRef<{context: string; active: boolean}>({context, active: false});
  useEffect(() => { flight.current = {context, active: false}; setSaving(false); }, [context]);

  useEffect(() => {
    if (!open) return;
    if (!account && initializedCreate.current === (contextKey ?? "create")) return;
    if (!account) initializedCreate.current = contextKey ?? "create";
    form.setFieldsValue({
      display_name: account?.display_name ?? "",
      stage: account?.stage ?? "lead",
      owner_user_id: account?.owner_user_id ?? undefined,
      industry_note: account?.industry_note ?? undefined,
      region_note: account?.region_note ?? undefined,
      next_follow_up_at: localDateTime(account?.next_follow_up_at),
    });
  }, [account, form, open, contextKey]);

  const submit = async () => {
    if (!open || !current() || (flight.current.context === context && flight.current.active)) return;
    const invocation = {context, active: true}; flight.current = invocation;
    setSaving(true);
    try {
      let values: AccountFormValues;
      try { values = await form.validateFields(); } catch { return; }
      if (!current()) return;
      await onSubmit({
        display_name: values.display_name.trim(),
        stage: values.stage,
        owner_user_id: values.owner_user_id?.trim() || null,
        industry_note: values.industry_note?.trim() || null,
        region_note: values.region_note?.trim() || null,
        next_follow_up_at: isoDateTime(values.next_follow_up_at),
      });
    } finally {
      invocation.active = false;
      if (current()) setSaving(false);
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
      onCancel={() => { if (!flight.current.active) onCancel(); }}
      closable={!saving}
      maskClosable={!saving}
      keyboard={!saving}
      cancelButtonProps={{disabled: saving}}
      afterOpenChange={(visible) => {
        if (!visible && account) form.resetFields();
      }}
    >
      {errorMessage && <Alert type="error" showIcon message={errorMessage} style={{marginBottom: 16}} />}
      <Typography.Paragraph type="secondary">
        仅录入内部合成或 Fixture 数据，不录入真实客户与联系人信息。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false} disabled={saving}>
        <Form.Item
          name="display_name"
          label="档案名称"
          rules={[{ required: true, whitespace: true, message: "请输入档案名称" }, { max: 200 }]}
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
