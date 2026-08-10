import { useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import type { ReviewDisagreementInput } from "../types";

interface Props {
  open: boolean;
  reviewStatus: "acknowledged" | "waived";
  onCancel: () => void;
  onSubmit: (input: ReviewDisagreementInput) => Promise<void>;
}

export default function DisagreementReviewModal({
  open,
  reviewStatus,
  onCancel,
  onSubmit,
}: Props) {
  const [form] = Form.useForm<{ review_note: string }>();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await onSubmit({ review_status: reviewStatus, review_note: values.review_note.trim() });
    } finally {
      setSaving(false);
    }
  };

  const waive = reviewStatus === "waived";
  return (
    <Modal
      open={open}
      title={waive ? "豁免合成分歧" : "确认合成分歧"}
      okText={waive ? "记录豁免" : "记录确认"}
      cancelText="取消"
      okButtonProps={{ danger: waive }}
      confirmLoading={saving}
      onCancel={onCancel}
      onOk={() => void submit()}
      afterOpenChange={(visible) => {
        if (!visible) form.resetFields();
      }}
    >
      <Typography.Paragraph type="secondary">
        人工处置只更新分歧审核状态，不会修改原始 failed result 或把失败改为通过。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="review_note" label={waive ? "豁免原因" : "确认说明"} rules={[{ required: true, message: "请输入处置说明" }, { max: 2000 }]}>
          <Input.TextArea rows={5} maxLength={2000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
