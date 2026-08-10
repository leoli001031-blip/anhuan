import { Empty, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";

interface CountRow {
  source: string;
  count: number;
}

export default function SourceCounts({ counts }: { counts: Record<string, number> }) {
  const rows = Object.entries(counts)
    .map(([source, count]) => ({ source, count }))
    .sort((left, right) => left.source.localeCompare(right.source));
  const columns: TableColumnsType<CountRow> = [
    {
      title: "来源类型",
      dataIndex: "source",
      render: (value: string) => value.replaceAll("_", " "),
    },
    {
      title: "记录数",
      dataIndex: "count",
      width: 120,
      align: "right",
      render: (value: number) => <Typography.Text strong>{value}</Typography.Text>,
    },
  ];
  return rows.length === 0 ? (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前快照没有来源计数" />
  ) : (
    <Table<CountRow>
      size="small"
      rowKey="source"
      dataSource={rows}
      columns={columns}
      pagination={false}
    />
  );
}
