import { useEffect, useState } from "react";
import { Form, Input, InputNumber, Modal, Select, Switch, Typography } from "antd";
import type { CreateRehearsalCheckInput, RehearsalCheck, UpdateRehearsalCheckInput } from "../types";

interface Props {
  open: boolean;
  check?: RehearsalCheck | null;
  onCancel: () => void;
  onSubmit: (input: CreateRehearsalCheckInput | UpdateRehearsalCheckInput) => Promise<void>;
}

export default function RehearsalCheckModal({ open, check, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<CreateRehearsalCheckInput>();
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({
      check_key: check?.check_key ?? "",
      category: check?.category ?? "service",
      label: check?.label ?? "",
      sequence_no: check?.sequence_no ?? 10,
      required: check?.required ?? true,
      enabled: check?.enabled ?? true,
    });
  }, [check, form, open]);
  const submit = async () => {
    const values = await form.validateFields();
    const shared = {
      category: values.category,
      label: values.label.trim(),
      sequence_no: values.sequence_no,
      required: values.required,
      enabled: values.enabled,
    };
    setSaving(true);
    try { await onSubmit(check ? shared : { check_key: values.check_key.trim(), ...shared }); } finally { setSaving(false); }
  };
  return (
    <Modal open={open} width="min(680px, 100vw)" title={check ? "编辑演练检查项" : "登记演练检查项"} okText={check ? "保存" : "登记"} cancelText="取消" confirmLoading={saving} onCancel={onCancel} onOk={() => void submit()} afterOpenChange={(visible) => { if (!visible) form.resetFields(); }}>
      <Typography.Paragraph type="secondary">检查项描述人工计划，不包含命令、凭据、绝对路径或日志正文。</Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
          <Form.Item name="check_key" label="检查键" rules={[{ required: true, message: "请输入检查键" }, { pattern: /^[a-z0-9][a-z0-9._-]{0,79}$/, message: "最多80位，仅允许小写字母、数字、点、_和-" }]}><Input disabled={Boolean(check)} autoComplete="off" /></Form.Item>
          <Form.Item name="category" label="类别" rules={[{ required: true }]}><Select options={[
            { value: "service", label: "服务" }, { value: "dependency", label: "依赖" }, { value: "backup", label: "备份" },
            { value: "restore", label: "恢复" }, { value: "security", label: "安全" }, { value: "rollback", label: "回滚" },
          ]} /></Form.Item>
          <Form.Item name="sequence_no" label="顺序" rules={[{ required: true }]}><InputNumber min={1} max={10000} precision={0} style={{ width: "100%" }} /></Form.Item>
          <Form.Item name="required" label="必需项" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </div>
        <Form.Item name="label" label="检查项名称" rules={[{ required: true, message: "请输入检查项名称" }, { max: 200 }]}><Input autoComplete="off" maxLength={200} /></Form.Item>
      </Form>
    </Modal>
  );
}
