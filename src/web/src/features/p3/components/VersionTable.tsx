import { Button, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { formatBytes, formatDateTime, mimeCopy } from "../reasonCopy";
import type { VersionSummary } from "../types";
import IngestionStatus from "./IngestionStatus";

interface VersionTableProps {
  versions: VersionSummary[];
  selectedVersionId: string | null;
  onSelect: (version: VersionSummary) => void;
}
export default function VersionTable({
  versions,
  selectedVersionId,
  onSelect,
}: VersionTableProps) {
  const columns: TableColumnsType<VersionSummary> = [
    {
      title: "版本",
      dataIndex: "version_number",
      width: 82,
      fixed: "left",
      render: (value: number) => `v${value}`,
    },
    {
      title: "源文件",
      dataIndex: "original_filename",
      width: 220,
      ellipsis: true,
    },
    {
      title: "格式",
      dataIndex: "content_type",
      width: 90,
      render: mimeCopy,
    },
    {
      title: "大小",
      dataIndex: "size_bytes",
      width: 110,
      render: formatBytes,
    },
    {
      title: "状态",
      key: "status",
      width: 250,
      render: (_, version) => <IngestionStatus version={version} compact />,
    },
    {
      title: "上传时间",
      dataIndex: "created_at",
      width: 180,
      render: formatDateTime,
    },
    {
      title: "操作",
      key: "action",
      width: 90,
      fixed: "right",
      render: (_, version) => (
        <Button type="link" onClick={() => onSelect(version)}>
          {selectedVersionId === version.id ? "查看中" : "查看"}
        </Button>
      ),
    },
  ];

  return versions.length === 0 ? (
    <Typography.Text type="secondary">尚未上传任何版本</Typography.Text>
  ) : (
    <Table<VersionSummary>
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={[...versions].sort((left, right) => right.version_number - left.version_number)}
      pagination={false}
      scroll={{ x: 1020 }}
      rowClassName={(version) =>
        version.id === selectedVersionId ? "ant-table-row-selected" : ""
      }
      onRow={(version) => ({ onClick: () => onSelect(version) })}
    />
  );
}
