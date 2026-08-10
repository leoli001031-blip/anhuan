import { Badge, Typography } from "antd";
import { useInternalPwaStatus } from "../hooks/useInternalPwaStatus";

export default function OnlineOfflineBadge({ compact = false }: { compact?: boolean }) {
  const { online } = useInternalPwaStatus();
  return (
    <Badge status={online ? "success" : "error"} text={compact ? undefined : <Typography.Text>{online ? "在线" : "离线"}</Typography.Text>} title={online ? "业务数据在线可用" : "离线仅可打开静态应用壳，业务数据不可用"} />
  );
}
