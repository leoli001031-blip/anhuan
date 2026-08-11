import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../../../api";
import { useAuth } from "../../../auth/OidcProvider";
import DocumentUploadModal from "../components/DocumentUploadModal";
import IngestionStatus from "../components/IngestionStatus";
import ResourceLimitsCard from "../components/ResourceLimitsCard";
import {
  getIngestionCapabilities,
  listIngestionDocuments,
  userFacingIngestionError,
} from "../ingestionApi";
import { formatDateTime, mimeCopy, statusColor, statusCopy } from "../reasonCopy";
import type {
  DocumentCollection,
  DocumentSummary,
  IngestionCapabilities,
} from "../types";

const EMPTY_COLLECTION: DocumentCollection = {
  items: [],
  next_cursor: null,
  allowed_actions: [],
};

export default function DocumentLibraryPage() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [collection, setCollection] = useState<DocumentCollection>(EMPTY_COLLECTION);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [contentTypeFilter, setContentTypeFilter] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const activeInitial = useRef<AbortController | null>(null);
  const activeMore = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    activeInitial.current?.abort();
    activeMore.current?.abort();
    setCollection(EMPTY_COLLECTION);
    setCapabilities(null);
    setError(null);
    if (!getSelectedEnterprise()) {
      setLoading(false);
      setError("请先在顶部选择企业");
      return;
    }
    const controller = new AbortController();
    activeInitial.current = controller;
    setLoading(true);
    try {
      const [nextCapabilities, nextCollection] = await Promise.all([
        getIngestionCapabilities(getAccessToken(), controller.signal),
        listIngestionDocuments(
          getAccessToken(),
          { status: statusFilter, contentType: contentTypeFilter, limit: 20 },
          controller.signal,
        ),
      ]);
      if (!controller.signal.aborted) {
        setCapabilities(nextCapabilities);
        setCollection(nextCollection);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
    } finally {
      if (activeInitial.current === controller) activeInitial.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [contentTypeFilter, getAccessToken, statusFilter]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => {
      setCollection(EMPTY_COLLECTION);
      setCapabilities(null);
      setUploadOpen(false);
      void refresh();
    };
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => {
      window.removeEventListener("f1-enterprise-changed", handleTenantChange);
      activeInitial.current?.abort();
      activeMore.current?.abort();
    };
  }, [refresh]);

  const loadMore = async () => {
    if (!collection.next_cursor || loadingMore) return;
    const controller = new AbortController();
    activeMore.current?.abort();
    activeMore.current = controller;
    setLoadingMore(true);
    try {
      const next = await listIngestionDocuments(
        getAccessToken(),
        {
          status: statusFilter,
          contentType: contentTypeFilter,
          cursor: collection.next_cursor,
          limit: 20,
        },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setCollection((current) => {
          const seen = new Set(current.items.map((item) => item.id));
          return {
            items: [...current.items, ...next.items.filter((item) => !seen.has(item.id))],
            next_cursor: next.next_cursor,
            allowed_actions: next.allowed_actions,
          };
        });
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
    } finally {
      if (activeMore.current === controller) activeMore.current = null;
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  };

  const canCreate =
    capabilities?.upload_enabled === true &&
    collection.allowed_actions.includes("create_document");

  const columns: TableColumnsType<DocumentSummary> = [
    {
      title: "文档",
      dataIndex: "display_name",
      width: 260,
      fixed: "left",
      render: (value: string, document) => (
        <Button type="link" onClick={() => navigate("/controlled-documents/" + document.id)}>
          {value}
        </Button>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{statusCopy(value)}</Tag>,
    },
    {
      title: "最新版本",
      key: "latest-version",
      width: 100,
      render: (_, document) =>
        document.latest_version ? `v${document.latest_version.version_number}` : "—",
    },
    {
      title: "格式",
      key: "content-type",
      width: 100,
      render: (_, document) =>
        document.latest_version ? mimeCopy(document.latest_version.content_type) : "—",
    },
    {
      title: "版本数",
      dataIndex: "version_count",
      width: 90,
    },
    {
      title: "处理状态",
      key: "ingestion-status",
      width: 260,
      render: (_, document) =>
        document.latest_version ? (
          <IngestionStatus version={document.latest_version} compact />
        ) : (
          "—"
        ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 180,
      render: formatDateTime,
    },
    {
      title: "操作",
      key: "action",
      width: 90,
      fixed: "right",
      render: (_, document) => (
        <Button type="link" onClick={() => navigate("/controlled-documents/" + document.id)}>
          查看
        </Button>
      ),
    },
  ];

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        wrap
        align="center"
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            受控文档库
          </Typography.Title>
          <Typography.Text type="secondary">
            新文件先进入隔离区，经本地扫描与安全预览后再解除隔离
          </Typography.Text>
        </div>
        <Space wrap>
          <Button
            data-testid="ingestion-refresh"
            icon={<ReloadOutlined />}
            onClick={() => void refresh()}
            disabled={loading}
          >
            刷新
          </Button>
          {canCreate && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setUploadOpen(true)}>
              新建文档
            </Button>
          )}
        </Space>
      </Space>

      <ResourceLimitsCard capabilities={capabilities} loading={loading} />

      <Space wrap style={{ marginBlock: 16 }}>
        <Select
          allowClear
          placeholder="全部处理状态"
          value={statusFilter}
          style={{ width: 170 }}
          options={[
            { value: "processing", label: "处理中" },
            { value: "ready", label: "可用" },
            { value: "blocked", label: "已阻断" },
            { value: "failed", label: "失败" },
          ]}
          onChange={setStatusFilter}
        />
        <Select
          allowClear
          placeholder="全部文件格式"
          value={contentTypeFilter}
          style={{ width: 180 }}
          options={(capabilities?.allowed_types ?? []).map((item) => ({
            value: item.content_type,
            label: item.extensions.map((ext) => ext.toUpperCase()).join(" / "),
          }))}
          onChange={setContentTypeFilter}
        />
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="文档库加载失败"
          description={error}
          action={<Button onClick={() => void refresh()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 320, display: "grid", placeItems: "center" }}>
          <Spin tip="正在加载受控文档库" />
        </div>
      ) : collection.items.length === 0 && !error ? (
        <Empty description="当前企业尚未导入文档">
          {canCreate && (
            <Button type="primary" onClick={() => setUploadOpen(true)}>
              上传第一个文档
            </Button>
          )}
        </Empty>
      ) : (
        <>
          <Table<DocumentSummary>
            rowKey="id"
            dataSource={collection.items}
            columns={columns}
            pagination={false}
            scroll={{ x: 1190 }}
            onRow={(document) => ({
              onDoubleClick: () => navigate("/controlled-documents/" + document.id),
            })}
          />
          {collection.next_cursor && (
            <div style={{ textAlign: "center", paddingTop: 16 }}>
              <Button loading={loadingMore} onClick={() => void loadMore()}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}

      <DocumentUploadModal
        open={uploadOpen}
        mode="create"
        token={getAccessToken()}
        capabilities={capabilities}
        onCancel={() => setUploadOpen(false)}
        onSuccess={({ documentId }) => {
          setUploadOpen(false);
          navigate("/controlled-documents/" + documentId);
        }}
      />
    </div>
  );
}
