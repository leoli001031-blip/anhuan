import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { useNavigate, useParams } from "react-router-dom";
import { getSelectedEnterprise } from "../../../api";
import { useAuth } from "../../../auth/OidcProvider";
import DocumentUploadModal from "../components/DocumentUploadModal";
import IngestionStatus from "../components/IngestionStatus";
import MaterialAnalysisPanel from "../components/MaterialAnalysisPanel";
import PreviewPanel from "../components/PreviewPanel";
import ResourceLimitsCard from "../components/ResourceLimitsCard";
import VersionTable from "../components/VersionTable";
import {
  getIngestionCapabilities,
  getIngestionDocument,
  processIngestionVersion,
  rejectIngestionVersion,
  releaseIngestionVersion,
  retryIngestionVersion,
  userFacingIngestionError,
} from "../ingestionApi";
import {
  formatBytes,
  formatDateTime,
  mimeCopy,
  reasonCopy,
  statusColor,
  statusCopy,
} from "../reasonCopy";
import type { DocumentDetail, IngestionCapabilities, VersionSummary } from "../types";

type VersionAction = "process" | "retry" | "release" | "reject";

function isStillProcessing(version: VersionSummary): boolean {
  return (
    version.workflow_status === "processing" ||
    version.scan_status === "scanning" ||
    ["queued", "generating"].includes(version.preview_status)
  );
}

export default function DocumentDetailPage() {
  const { getAccessToken } = useAuth();
  const { documentId } = useParams<{ documentId: string }>();
  const navigate = useNavigate();
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const capabilitiesRef = useRef<IngestionCapabilities | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [activeAction, setActiveAction] = useState<VersionAction | null>(null);
  const activeLoad = useRef<AbortController | null>(null);
  const activeMutation = useRef<AbortController | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      activeLoad.current?.abort();
      if (!documentId) {
        setLoading(false);
        setError("文档标识缺失");
        return;
      }
      if (!getSelectedEnterprise()) {
        setDocument(null);
        setCapabilities(null);
        capabilitiesRef.current = null;
        setLoading(false);
        setError("请先在顶部选择企业");
        return;
      }
      const controller = new AbortController();
      activeLoad.current = controller;
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const capabilityPromise = capabilitiesRef.current
          ? Promise.resolve(capabilitiesRef.current)
          : getIngestionCapabilities(getAccessToken(), controller.signal);
        const [nextCapabilities, nextDocument] = await Promise.all([
          capabilityPromise,
          getIngestionDocument(getAccessToken(), documentId, controller.signal),
        ]);
        if (!controller.signal.aborted) {
          capabilitiesRef.current = nextCapabilities;
          setCapabilities(nextCapabilities);
          setDocument(nextDocument);
          setSelectedVersionId((current) => {
            if (current && nextDocument.versions.some((version) => version.id === current)) {
              return current;
            }
            return nextDocument.latest_version?.id ?? nextDocument.versions[0]?.id ?? null;
          });
          setError(null);
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
      } finally {
        if (activeLoad.current === controller) activeLoad.current = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [documentId, getAccessToken],
  );

  useEffect(() => {
    void refresh(false);
    const handleTenantChange = () => {
      activeLoad.current?.abort();
      activeMutation.current?.abort();
      capabilitiesRef.current = null;
      setCapabilities(null);
      setDocument(null);
      setSelectedVersionId(null);
      setUploadOpen(false);
      setActiveAction(null);
      void refresh(false);
    };
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => {
      window.removeEventListener("f1-enterprise-changed", handleTenantChange);
      activeLoad.current?.abort();
      activeMutation.current?.abort();
    };
  }, [refresh]);

  const shouldPoll = document?.versions.some(isStillProcessing) ?? false;
  useEffect(() => {
    if (!shouldPoll) return;
    const timeout = window.setTimeout(() => void refresh(true), 2500);
    return () => window.clearTimeout(timeout);
  }, [document?.updated_at, refresh, shouldPoll]);

  const selectedVersion =
    document?.versions.find((version) => version.id === selectedVersionId) ?? null;

  const performAction = async (action: VersionAction) => {
    if (!selectedVersion || !selectedVersion.allowed_actions.includes(action)) return;
    const controller = new AbortController();
    activeMutation.current?.abort();
    activeMutation.current = controller;
    setActiveAction(action);
    setError(null);
    try {
      if (action === "process") {
        await processIngestionVersion(getAccessToken(), selectedVersion.id, controller.signal);
      } else if (action === "retry") {
        await retryIngestionVersion(getAccessToken(), selectedVersion.id, controller.signal);
      } else if (action === "release") {
        await releaseIngestionVersion(getAccessToken(), selectedVersion.id, controller.signal);
      } else {
        await rejectIngestionVersion(getAccessToken(), selectedVersion.id, controller.signal);
      }
      if (!controller.signal.aborted) {
        message.success(
          action === "process"
            ? "已开始安全处理"
            : action === "retry"
            ? "已重新提交处理"
            : action === "release"
              ? "已解除隔离"
              : "已拒绝，隔离记录继续保留",
        );
        await refresh(true);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
    } finally {
      if (activeMutation.current === controller) activeMutation.current = null;
      if (!controller.signal.aborted) setActiveAction(null);
    }
  };

  if (loading && !document) {
    return (
      <div style={{ minHeight: 420, display: "grid", placeItems: "center" }}>
        <Spin tip="正在加载文档详情" />
      </div>
    );
  }

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        wrap
        align="center"
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <Space align="start">
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            aria-label="返回文档库"
            onClick={() => navigate("/controlled-documents")}
          />
          <div>
            <Typography.Title level={3} style={{ marginBottom: 4 }}>
              {document?.display_name ?? "文档详情"}
            </Typography.Title>
            <Typography.Text type="secondary">
              查看隔离、扫描、版本与安全预览状态
            </Typography.Text>
          </div>
        </Space>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => void refresh(false)} disabled={loading}>
            刷新
          </Button>
          {document?.allowed_actions.includes("upload_version") &&
            capabilities?.upload_enabled && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setUploadOpen(true)}
              >
                上传新版本
              </Button>
            )}
        </Space>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="文档操作未完成"
          description={error}
          action={<Button onClick={() => void refresh(false)}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {!document ? (
        <Empty description="文档不存在或当前企业无权访问">
          <Button onClick={() => navigate("/controlled-documents")}>返回文档库</Button>
        </Empty>
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <ResourceLimitsCard capabilities={capabilities} />

          <Card size="small">
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2, lg: 4 }}
              items={[
                {
                  key: "status",
                  label: "文档状态",
                  children: (
                    <Tag color={statusColor(document.status)}>{statusCopy(document.status)}</Tag>
                  ),
                },
                { key: "versions", label: "版本数", children: document.version_count },
                {
                  key: "scope",
                  label: "材料归属",
                  children: document.knowledge_scope.kind === "client"
                    ? `客户资料 · ${document.knowledge_scope.client_display_name ?? "客户档案"}`
                    : "当前环保服务公司",
                },
                { key: "created", label: "创建时间", children: formatDateTime(document.created_at) },
                { key: "updated", label: "更新时间", children: formatDateTime(document.updated_at) },
              ]}
            />
          </Card>

          <Card title="版本记录">
            <VersionTable
              versions={document.versions}
              selectedVersionId={selectedVersionId}
              onSelect={(version) => setSelectedVersionId(version.id)}
            />
          </Card>

          {selectedVersion ? (
            <>
              <Card
                title={`版本 v${selectedVersion.version_number}`}
                extra={
                  <Space wrap>
                    {selectedVersion.allowed_actions.includes("process") && (
                      <Button
                        type="primary"
                        loading={activeAction === "process"}
                        disabled={activeAction !== null && activeAction !== "process"}
                        onClick={() => void performAction("process")}
                      >
                        开始安全处理
                      </Button>
                    )}
                    {selectedVersion.allowed_actions.includes("retry") && (
                      <Button
                        loading={activeAction === "retry"}
                        disabled={activeAction !== null && activeAction !== "retry"}
                        onClick={() => void performAction("retry")}
                      >
                        重试
                      </Button>
                    )}
                    {selectedVersion.allowed_actions.includes("release") && (
                      <Popconfirm
                        title="确认解除隔离？"
                        description="仅扫描干净且安全预览完成的版本可以解除隔离。"
                        okText="解除隔离"
                        cancelText="取消"
                        onConfirm={() => performAction("release")}
                      >
                        <Button
                          type="primary"
                          loading={activeAction === "release"}
                          disabled={activeAction !== null && activeAction !== "release"}
                        >
                          解除隔离
                        </Button>
                      </Popconfirm>
                    )}
                    {selectedVersion.allowed_actions.includes("reject") && (
                      <Popconfirm
                        title="确认拒绝该版本？"
                        description="拒绝不会删除隔离对象和处理记录。"
                        okText="拒绝并保留"
                        cancelText="取消"
                        onConfirm={() => performAction("reject")}
                      >
                        <Button
                          danger
                          loading={activeAction === "reject"}
                          disabled={activeAction !== null && activeAction !== "reject"}
                        >
                          拒绝并保留
                        </Button>
                      </Popconfirm>
                    )}
                  </Space>
                }
              >
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <IngestionStatus version={selectedVersion} />
                  {selectedVersion.reason_code && (
                    <Alert
                      type={selectedVersion.scan_status === "infected" ? "error" : "warning"}
                      showIcon
                      message={reasonCopy(selectedVersion.reason_code)}
                    />
                  )}
                  <Descriptions
                    size="small"
                    column={{ xs: 1, sm: 2, lg: 4 }}
                    items={[
                      {
                        key: "filename",
                        label: "源文件",
                        children: selectedVersion.original_filename,
                      },
                      {
                        key: "type",
                        label: "格式",
                        children: mimeCopy(selectedVersion.content_type),
                      },
                      {
                        key: "size",
                        label: "大小",
                        children: formatBytes(selectedVersion.size_bytes),
                      },
                      {
                        key: "time",
                        label: "上传时间",
                        children: formatDateTime(selectedVersion.created_at),
                      },
                    ]}
                  />
                </Space>
              </Card>
              <PreviewPanel token={getAccessToken()} version={selectedVersion} />
              <MaterialAnalysisPanel token={getAccessToken()} version={selectedVersion} />
            </>
          ) : (
            <Empty description="请选择一个版本查看状态与预览" />
          )}
        </Space>
      )}

      <DocumentUploadModal
        open={uploadOpen}
        mode="version"
        token={getAccessToken()}
        documentId={document?.id}
        capabilities={capabilities}
        onCancel={() => setUploadOpen(false)}
        onSuccess={({ versionId }) => {
          setUploadOpen(false);
          if (versionId) setSelectedVersionId(versionId);
          void refresh(true);
        }}
      />
    </div>
  );
}
