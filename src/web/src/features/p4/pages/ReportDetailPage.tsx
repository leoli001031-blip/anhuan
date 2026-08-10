import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Grid,
  List,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import ReportVersionModal from "../components/ReportVersionModal";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import {
  formatP4Bytes,
  formatP4DateTime,
  lifecycleColor,
  reportStatusCopy,
  versionLifecycleCopy,
} from "../reasonCopy";
import type { BusinessReportDetail, CreateReportVersionInput, ReportVersionSummary } from "../types";
import {
  archiveBusinessReport,
  createReportVersion,
  getBusinessReport,
  isViewsReportsRequestAborted,
  userFacingViewsReportsError,
} from "../viewsReportsApi";

const EMPTY_REPORT: BusinessReportDetail = {
  id: "",
  service_case_id: "",
  title: "",
  status: "active",
  current_version_no: 0,
  created_at: "",
  updated_at: "",
  allowed_actions: [],
  versions: [],
};

export default function ReportDetailPage() {
  const { reportId = "" } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [versionOpen, setVersionOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getBusinessReport(token, reportId, signal),
    [reportId],
  );
  const { data, loading, error, setError, reload, runMutation, tenantEpoch } =
    useP4TenantQuery(EMPTY_REPORT, load);

  useEffect(() => {
    setVersionOpen(false);
    setArchiveOpen(false);
  }, [tenantEpoch]);

  const handleCreateVersion = async (input: CreateReportVersionInput) => {
    setError(null);
    try {
      const created = await runMutation((token, signal) =>
        createReportVersion(token, reportId, input, signal),
      );
      setVersionOpen(false);
      navigate(`/reports/${reportId}/versions/${created.id}`);
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    }
  };

  const handleArchive = async () => {
    setError(null);
    setArchiving(true);
    try {
      await runMutation((token, signal) => archiveBusinessReport(token, reportId, signal));
      setArchiveOpen(false);
      await reload();
    } catch (reason) {
      if (!isViewsReportsRequestAborted(reason)) setError(userFacingViewsReportsError(reason));
    } finally {
      setArchiving(false);
    }
  };

  const columns: TableColumnsType<ReportVersionSummary> = [
    { title: "版本", dataIndex: "version_number", width: 90, render: (value: number) => `v${value}` },
    {
      title: "生命周期",
      dataIndex: "lifecycle",
      width: 130,
      render: (value: string) => <Tag color={lifecycleColor(value)}>{versionLifecycleCopy(value)}</Tag>,
    },
    { title: "版本说明", dataIndex: "change_note", width: 260, ellipsis: true, render: (value: string | null) => value ?? "—" },
    { title: "来源类型", dataIndex: "source_counts", width: 110, render: (value: Record<string, number>) => Object.keys(value).length },
    { title: "快照大小", dataIndex: "snapshot_size_bytes", width: 120, render: formatP4Bytes },
    { title: "捕获时间", dataIndex: "captured_at", width: 180, render: formatP4DateTime },
    {
      title: "操作",
      key: "actions",
      width: 90,
      fixed: "right",
      render: (_, version) =>
        version.allowed_actions.includes("view") ? (
          <Button type="link" onClick={() => navigate(`/reports/${reportId}/versions/${version.id}`)}>查看</Button>
        ) : null,
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/reports")}>← 返回业务报告</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>{data.title || "业务报告"}</Typography.Title>
            {!loading && <Tag color={data.status === "active" ? "green" : "default"}>{reportStatusCopy(data.status)}</Tag>}
          </Space>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
          {data.allowed_actions.includes("archive") && <Button danger onClick={() => setArchiveOpen(true)}>归档报告</Button>}
          {data.allowed_actions.includes("create_version") && (
            <Button type="primary" onClick={() => setVersionOpen(true)}>捕获新版本</Button>
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
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载业务报告" /></div>
      ) : !data.id && !error ? (
        <Empty description="业务报告不存在" />
      ) : (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="服务任务 ID">{data.service_case_id}</Descriptions.Item>
            <Descriptions.Item label="当前版本">v{data.current_version_no}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatP4DateTime(data.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatP4DateTime(data.updated_at)}</Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p4-report-versions-heading">
            <Typography.Title id="p4-report-versions-heading" level={4}>不可变版本</Typography.Title>
            {data.versions.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无快照版本" />
            ) : screens.md ? (
              <Table<ReportVersionSummary> rowKey="id" dataSource={data.versions} columns={columns} pagination={false} scroll={{ x: 1060 }} />
            ) : (
              <List
                dataSource={data.versions}
                renderItem={(version) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                        <Typography.Text strong>v{version.version_number}</Typography.Text>
                        <Tag color={lifecycleColor(version.lifecycle)}>{versionLifecycleCopy(version.lifecycle)}</Tag>
                      </Space>
                      <Typography.Paragraph style={{ margin: "8px 0" }}>{version.change_note ?? "无版本说明"}</Typography.Paragraph>
                      <Typography.Text type="secondary">{formatP4DateTime(version.captured_at)} · {formatP4Bytes(version.snapshot_size_bytes)}</Typography.Text>
                      {version.allowed_actions.includes("view") && (
                        <Button block style={{ marginTop: 12 }} onClick={() => navigate(`/reports/${reportId}/versions/${version.id}`)}>查看快照元数据</Button>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </section>
        </Space>
      )}

      <ReportVersionModal
        open={versionOpen}
        nextVersionNumber={data.current_version_no + 1}
        onCancel={() => setVersionOpen(false)}
        onSubmit={handleCreateVersion}
      />
      <Modal
        open={archiveOpen}
        title="归档业务报告"
        okText="确认归档"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={archiving}
        onOk={() => void handleArchive()}
        onCancel={() => setArchiveOpen(false)}
      >
        归档后已有快照仍可查看，但不能继续捕获新版本。
      </Modal>
    </div>
  );
}
