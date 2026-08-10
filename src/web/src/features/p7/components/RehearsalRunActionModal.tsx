import { useState } from "react";
import { Alert, Modal, Typography } from "antd";

interface Props {
  open: boolean;
  action: "complete" | "cancel";
  rollbackRequired: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}

export default function RehearsalRunActionModal({ open, action, rollbackRequired, onCancel, onConfirm }: Props) {
  const [saving, setSaving] = useState(false);
  const complete = action === "complete";
  const confirm = async () => {
    setSaving(true);
    try { await onConfirm(); } finally { setSaving(false); }
  };
  return (
    <Modal open={open} title={complete ? "完成本地演练" : "取消本地演练"} okText={complete ? "确认完成" : "确认取消"} cancelText="返回" okButtonProps={{ danger: !complete || rollbackRequired }} confirmLoading={saving} onCancel={onCancel} onOk={() => void confirm()}>
      {rollbackRequired && <Alert type="error" showIcon message="ROLLBACK REQUIRED" description="当前 run 含失败或阻断的必需项，完成门只能形成 failed，并明确保留回滚要求。" style={{ marginBottom: 16 }} />}
      <Typography.Paragraph type="secondary">该操作只关闭人工计划记录，不会执行回滚、恢复、部署或任何生产动作。</Typography.Paragraph>
    </Modal>
  );
}
