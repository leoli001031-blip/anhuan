import { useCallback } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import ArtifactMetadata from "../components/ArtifactMetadata";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import SourceCounts from "../components/SourceCounts";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import {
  formatP4Bytes,
  formatP4DateTime,
  lifecycleColor,
  versionLifecycleCopy,
} from "../reasonCopy";
import type { ReportVersionDetail } from "../types";
import { getReportVersion } from "../viewsReportsApi";

interface SnapshotShapeRow {
  section: string;
  records: number;
}

function canonicalSnapshotShape(snapshot: unknown): SnapshotShapeRow[] {
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) return [];
  return Object.entries(snapshot as Record<string, unknown>)
    .map(([section, value]) => ({
      section,
      records: Array.isArray(value) ? value.length : value && typeof value === "object" ? 1 : 0,
    }))
    .sort((left, right) => left.section.localeCompare(right.section));
}

export default function ReportVersionDetailPage() {
  const { reportId = "", versionId = "" } = useParams<{ reportId: string; versionId: string }>();
  const navigate = useNavigate();
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getReportVersion(token, versionId, signal),
    [versionId],
  );
  const { data, loading, error, reload } = useP4TenantQuery<ReportVersionDetail | null>(null, load);
  const artifactMatches = Boolean(
    data?.artifact &&
      data.artifact.sha256 === data.snapshot_sha256 &&
      data.artifact.size_bytes === data.snapshot_size_bytes,
  );
  const shapeRows = canonicalSnapshotShape(data?.canonical_snapshot);
  const shapeColumns: TableColumnsType<SnapshotShapeRow> = [
    { title: "快照区段", dataIndex: "section", render: (value: string) => value.replaceAll("_", " ") },
    { title: "聚合记录数", dataIndex: "records", width: 140, align: "right" },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate("/reports/" + reportId)}>← 返回报告版本</Button>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>
              {data ? `v${data.version_number} 快照元数据` : "报告快照元数据"}
            </Typography.Title>
            {data && <Tag color={lifecycleColor(data.lifecycle)}>{versionLifecycleCopy(data.lifecycle)}</Tag>}
          </Space>
        </div>
        <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
      </Space>

      <P4BoundaryBanner />

      {error && (
        <Alert
          type="error"
          showIcon
          message="报告版本加载失败"
          description={error}
          action={<Button onClick={() => void reload()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}><Spin tip="正在加载不可变快照元数据" /></div>
      ) : !data && !error ? (
        <Empty description="报告版本不存在" />
      ) : data ? (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
            <Descriptions.Item label="版本">v{data.version_number}</Descriptions.Item>
            <Descriptions.Item label="捕获时间">{formatP4DateTime(data.captured_at)}</Descriptions.Item>
            <Descriptions.Item label="版本说明">{data.change_note ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="快照大小">{formatP4Bytes(data.snapshot_size_bytes)}</Descriptions.Item>
            <Descriptions.Item label="Snapshot SHA-256" span={2}>
              <Typography.Text code style={{ overflowWrap: "anywhere" }}>{data.snapshot_sha256}</Typography.Text>
            </Descriptions.Item>
          </Descriptions>

          <section aria-labelledby="p4-snapshot-summary-heading">
            <Typography.Title id="p4-snapshot-summary-heading" level={4}>快照摘要</Typography.Title>
            <Typography.Paragraph type="secondary">
              仅展示 canonical JSON 顶层结构计数，不展开业务正文、文件名或对象位置。
            </Typography.Paragraph>
            {shapeRows.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前快照没有可展示的结构摘要" />
            ) : (
              <Table<SnapshotShapeRow> size="small" rowKey="section" dataSource={shapeRows} columns={shapeColumns} pagination={false} />
            )}
          </section>

          <section aria-labelledby="p4-source-counts-heading">
            <Typography.Title id="p4-source-counts-heading" level={4}>来源计数</Typography.Title>
            <SourceCounts counts={data.source_counts} />
          </section>

          <section aria-labelledby="p4-artifact-heading">
            <Space wrap align="center">
              <Typography.Title id="p4-artifact-heading" level={4} style={{ marginBottom: 12 }}>Canonical artifact 元数据</Typography.Title>
              <Tag color={artifactMatches ? "green" : "red"}>{artifactMatches ? "SHA / SIZE 一致" : "SHA / SIZE 不一致"}</Tag>
            </Space>
            <ArtifactMetadata artifact={data.artifact} />
          </section>
        </Space>
      ) : null}
    </div>
  );
}
