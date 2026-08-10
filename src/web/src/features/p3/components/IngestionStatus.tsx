import { Space, Tag, Tooltip, Typography } from "antd";
import { reasonCopy, statusColor, statusCopy } from "../reasonCopy";
import type { VersionSummary } from "../types";

interface IngestionStatusProps {
  version: VersionSummary;
  compact?: boolean;
}
export default function IngestionStatus({
  version,
  compact = false,
}: IngestionStatusProps) {
  const tags = compact
    ? [
        ["处理", version.workflow_status],
        ["扫描", version.scan_status],
      ]
    : [
        ["处理", version.workflow_status],
        ["隔离", version.quarantine_status],
        ["扫描", version.scan_status],
        ["预览", version.preview_status],
      ];

  return (
    <Space direction="vertical" size={compact ? 2 : 6}>
      <Space size={[4, 4]} wrap>
        {tags.map(([label, status]) => (
          <Tag key={label} color={statusColor(status)}>
            {label}：{statusCopy(status)}
          </Tag>
        ))}
      </Space>
      {!compact && version.reason_code && (
        <Tooltip title={version.reason_code}>
          <Typography.Text type="danger">
            {reasonCopy(version.reason_code)}
          </Typography.Text>
        </Tooltip>
      )}
    </Space>
  );
}
