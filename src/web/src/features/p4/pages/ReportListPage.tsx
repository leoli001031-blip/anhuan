import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Grid,
  List,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import ReportCreateModal from "../components/ReportCreateModal";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import { formatP4DateTime, reportStatusCopy } from "../reasonCopy";
import type { BusinessReport, BusinessReportCollection, CreateBusinessReportInput } from "../types";
import {
  createBusinessReport,
  isViewsReportsRequestAborted,
  listBusinessReports,
  userFacingViewsReportsError,
} from "../viewsReportsApi";

const EMPTY_REPORTS: BusinessReportCollection = { items: [], allowed_actions: [] };

export default function ReportListPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => listBusinessReports(token, signal),
    [],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP4TenantQuery(EMPTY_REPORTS, load);

  useEffect(() => setCreateOpen(false), [tenantEpoch]);

  const handleCreate = async (input: CreateBusinessReportInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createBusinessReport(token, input, signal),
      );
      setCreateOpen(false);
      navigate("/reports/" + created.id);
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const columns: TableColumnsType<BusinessReport> = [
    {
      title: "业务报告",
      dataIndex: "title",
      width: 300,
      fixed: "left",
      render: (value: string, report) =>
        report.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/reports/" + report.id)}>{value}</Button>
        ) : value,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <Tag color={value === "active" ? "green" : "default"}>{reportStatusCopy(value)}</Tag>,
    },
    { title: "当前版本", dataIndex: "current_version_no", width: 110, render: (value: number) => `v${value}` },
    { title: "版本数", dataIndex: "version_count", width: 90, render: (value: number | undefined) => value ?? "—" },
    { title: "服务任务 ID", dataIndex: "service_case_id", width: 240, ellipsis: true },
    { title: "更新时间", dataIndex: "updated_at", width: 180, render: formatP4DateTime },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, report) =>
        report.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate("/reports/" + report.id)}>查看</Button>
        ) : null,
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>业务报告快照</Typography.Title>
          <Typography.Text type="secondary">查看不可变版本、来源计数与 canonical JSON artifact 元数据</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("create") && (
            <Button type="primary" onClick={() => setCreateOpen(true)}>建立业务报告</Button>
          )}
        </Space>
      </Space>

      <P4BoundaryBanner />

      {error && (
        <Alert
          type="error"
          showIcon
          message="业务报告操作未完成"
          description={error}
          action={<Button onClick={() => void reload()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 320, display: "grid", placeItems: "center" }}><Spin tip="正在加载业务报告" /></div>
      ) : data.items.length === 0 && !error ? (
        <Empty description="当前企业尚无业务报告快照">
          {data.allowed_actions.includes("create") && (
            <Button type="primary" onClick={() => setCreateOpen(true)}>建立第一份报告</Button>
          )}
        </Empty>
      ) : screens.md ? (
        <Table<BusinessReport> rowKey="id" dataSource={data.items} columns={columns} pagination={false} scroll={{ x: 1120 }} />
      ) : (
        <List
          dataSource={data.items}
          renderItem={(report) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Typography.Text strong>{report.title}</Typography.Text>
                  <Tag color={report.status === "active" ? "green" : "default"}>{reportStatusCopy(report.status)}</Tag>
                </Space>
                <Typography.Paragraph type="secondary" style={{ margin: "8px 0" }}>
                  当前 v{report.current_version_no} · 更新于 {formatP4DateTime(report.updated_at)}
                </Typography.Paragraph>
                {report.allowed_actions.includes("view") && (
                  <Button block onClick={() => navigate("/reports/" + report.id)}>查看版本</Button>
                )}
              </div>
            </List.Item>
          )}
        />
      )}

      <ReportCreateModal open={createOpen} onCancel={() => setCreateOpen(false)} onSubmit={handleCreate} />
    </div>
  );
}
