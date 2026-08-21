// 状态呈现：小圆点 + 文字，管理端唯一状态图形。
import { Typography } from "antd";

export type StatusTone = "success" | "warning" | "danger" | "processing" | "neutral";

export default function StatusDot({
  tone,
  label,
}: {
  tone: StatusTone;
  label: string;
}) {
  return (
    <Typography.Text>
      <span className={`status-dot status-dot--${tone}`} />
      {label}
    </Typography.Text>
  );
}
