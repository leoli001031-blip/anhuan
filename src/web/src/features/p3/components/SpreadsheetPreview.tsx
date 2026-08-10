import { useEffect, useMemo, useState } from "react";
import { Alert, Empty, Pagination, Select, Space, Spin, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { getWorksheetGrid, userFacingIngestionError } from "../ingestionApi";
import { spreadsheetColumnLabel } from "../reasonCopy";
import type { PreviewUnit, WorksheetGrid } from "../types";

interface SpreadsheetPreviewProps {
  token: string | null;
  versionId: string;
  units: PreviewUnit[];
}
const PAGE_SIZE = 50;

export default function SpreadsheetPreview({
  token,
  versionId,
  units,
}: SpreadsheetPreviewProps) {
  const ordered = useMemo(
    () => [...units].sort((left, right) => left.ordinal - right.ordinal),
    [units],
  );
  const [unitId, setUnitId] = useState<string>(ordered[0]?.id ?? "");
  const [page, setPage] = useState(1);
  const [grid, setGrid] = useState<WorksheetGrid | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unitIds = ordered.map((unit) => unit.id).join(":");

  useEffect(() => {
    setUnitId(ordered[0]?.id ?? "");
    setPage(1);
    setGrid(null);
  }, [unitIds, versionId]);

  useEffect(() => {
    if (!unitId) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getWorksheetGrid(
      token,
      versionId,
      unitId,
      (page - 1) * PAGE_SIZE,
      PAGE_SIZE,
      controller.signal,
    )
      .then((payload) => {
        if (!controller.signal.aborted) setGrid(payload);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setGrid(null);
          setError(userFacingIngestionError(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [page, token, unitId, versionId]);

  if (ordered.length === 0) return <Empty description="没有可显示的工作表" />;

  const columns: TableColumnsType<Record<string, unknown>> = [
    {
      title: "行",
      dataIndex: "row_number",
      key: "row_number",
      width: 72,
      fixed: "left",
    },
    ...Array.from({ length: grid?.total_columns ?? 0 }, (_, index) => ({
      title: spreadsheetColumnLabel(index),
      dataIndex: "column_" + index,
      key: "column_" + index,
      width: 160,
      ellipsis: true,
      render: (value: unknown) =>
        value === null || value === undefined || value === "" ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          String(value)
        ),
    })),
  ];

  const dataSource = (grid?.rows ?? []).map((cells, rowIndex) => {
    const record: Record<string, unknown> = {
      key: `${grid?.row_offset ?? 0}-${rowIndex}`,
      row_number: (grid?.row_offset ?? 0) + rowIndex + 1,
    };
    cells.forEach((value, columnIndex) => {
      record["column_" + columnIndex] = value;
    });
    return record;
  });

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Typography.Text strong>工作表</Typography.Text>
        <Select
          value={unitId}
          style={{ minWidth: 200 }}
          options={ordered.map((unit) => ({ value: unit.id, label: unit.label }))}
          onChange={(value) => {
            setUnitId(value);
            setPage(1);
            setGrid(null);
          }}
        />
      </Space>
      {error && <Alert type="error" showIcon message="表格预览读取失败" description={error} />}
      {grid?.truncated && (
        <Alert type="info" showIcon message="该工作表仅展示资源限制范围内的内容" />
      )}
      {loading ? (
        <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}>
          <Spin tip="正在读取工作表" />
        </div>
      ) : dataSource.length === 0 && !error ? (
        <Empty description="当前工作表没有可显示的单元格" />
      ) : (
        <Table<Record<string, unknown>>
          size="small"
          columns={columns}
          dataSource={dataSource}
          pagination={false}
          scroll={{ x: Math.max(640, columns.length * 160), y: 480 }}
        />
      )}
      {grid && grid.total_rows > PAGE_SIZE && (
        <Pagination
          current={page}
          pageSize={PAGE_SIZE}
          total={grid.total_rows}
          showSizeChanger={false}
          onChange={setPage}
        />
      )}
    </Space>
  );
}
