import { useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type { PolicyReviewInput } from "../types";

type ReviewAction = "submit" | "approve" | "reject" | "publish";

const ACTION_COPY: Record<ReviewAction, { title: string; okText: string; note: string }> = {
  submit: { title: "提交内部审核", okText: "提交", note: "提交后候选进入审核队列，提交人不能审批自己本轮提交。" },
  approve: { title: "审核通过候选", okText: "通过", note: "通过只表示内部审核状态，不构成法规适用或专业结论。" },
  reject: { title: "退回版本候选", okText: "退回", note: "退回意见将作为 append-only 审核事件保留。" },
  publish: { title: "内部发布版本状态", okText: "内部发布", note: "内部发布不会对外分发、通知或生成正式签发材料。" },
};

interface Props {
  open: boolean;
  action: ReviewAction;
  onCancel: () => void;
  onSubmit: (input: PolicyReviewInput) => Promise<void>;
}

export default function PolicyReviewModal({ open, action, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<PolicyReviewInput>();
  const [saving, setSaving] = useState(false);
  const copy = ACTION_COPY[action];

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({ comment: values.comment?.trim() || null });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={copy.title}
      okText={copy.okText}
      cancelText="取消"
      okButtonProps={{ danger: action === "reject" }}
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">{copy.note}</Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item
          name="comment"
          label={action === "reject" ? "退回意见" : "内部审核备注"}
          rules={action === "reject" ? [{ required: true, message: "请输入退回意见" }] : []}
        >
          <Input.TextArea rows={4} maxLength={2000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
