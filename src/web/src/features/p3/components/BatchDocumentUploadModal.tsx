import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Input,
  List,
  Modal,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
} from "antd";
import { DeleteOutlined, InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import { createIngestionDocument, userFacingIngestionError } from "../ingestionApi";
import { formatBytes, reasonCopy } from "../reasonCopy";
import type {
  IngestionCapabilities,
  KnowledgeScopeTarget,
  MaterialKind,
} from "../types";

const MAX_BATCH_FILES = 10;
const MAX_CONCURRENT_UPLOADS = 2;

type BatchItemStatus = "queued" | "uploading" | "succeeded" | "failed";

interface BatchItem {
  uid: string;
  uploadFile: UploadFile;
  file: File;
  displayName: string;
  declaredMaterialKind: MaterialKind;
  idempotencyKey: string;
  status: BatchItemStatus;
  error: string | null;
  documentId: string | null;
  versionId: string | null;
}

interface Props {
  open: boolean;
  token: string | null;
  capabilities: IngestionCapabilities | null;
  knowledgeScope: KnowledgeScopeTarget;
  defaultMaterialKind?: MaterialKind;
  scopeHint?: string;
  onCancel: () => void;
  onComplete: () => void;
}

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function defaultDisplayName(filename: string): string {
  const withoutExtension = filename.replace(/\.[^.]+$/, "").trim();
  return (withoutExtension || filename).slice(0, 160);
}

function extensionOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index < 0 ? "" : filename.slice(index).toLowerCase();
}

function statusTag(status: BatchItemStatus): React.ReactNode {
  if (status === "uploading") return <Tag color="blue">上传中</Tag>;
  if (status === "succeeded") return <Tag color="green">已进入隔离区</Tag>;
  if (status === "failed") return <Tag color="red">未完成</Tag>;
  return <Tag color="gold">等待上传</Tag>;
}

export default function BatchDocumentUploadModal({
  open,
  token,
  capabilities,
  knowledgeScope,
  defaultMaterialKind = "unknown",
  scopeHint,
  onCancel,
  onComplete,
}: Props) {
  const [items, setItems] = useState<BatchItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const activeControllers = useRef(new Set<AbortController>());
  const batchEpoch = useRef(0);

  useEffect(() => {
    if (open) {
      setItems([]);
      setUploading(false);
      return;
    }
    for (const controller of activeControllers.current) controller.abort();
    activeControllers.current.clear();
    batchEpoch.current += 1;
    setUploading(false);
  }, [open, defaultMaterialKind, knowledgeScope.client_account_id, knowledgeScope.kind]);

  useEffect(
    () => () => {
      for (const controller of activeControllers.current) controller.abort();
      activeControllers.current.clear();
      batchEpoch.current += 1;
    },
    [],
  );

  const allowedExtensions = useMemo(
    () =>
      capabilities?.allowed_types.flatMap((item) =>
        item.extensions.map((extension) => extension.toLowerCase()),
      ) ?? [],
    [capabilities],
  );
  const hasPending = items.some((item) => item.status === "queued" || item.status === "failed");

  const updateItem = (uid: string, changes: Partial<BatchItem>) => {
    setItems((current) =>
      current.map((item) => (item.uid === uid ? { ...item, ...changes } : item)),
    );
  };

  const validateItem = (item: BatchItem): string | null => {
    if (!capabilities?.upload_enabled) {
      return reasonCopy(capabilities?.disabled_reason_code);
    }
    if (!item.displayName.trim()) return "请输入文档名称";
    if (item.displayName.trim().length > 160) return "文档名称不能超过 160 个字符";
    const extension = extensionOf(item.file.name);
    const format = capabilities.allowed_types.find((candidate) =>
      candidate.extensions.map((value) => value.toLowerCase()).includes(extension),
    );
    if (!format) return reasonCopy("FILE_TYPE_NOT_ALLOWED");
    if (
      item.file.size > format.max_file_bytes ||
      item.file.size > capabilities.limits.max_file_bytes
    ) {
      return reasonCopy("FILE_TOO_LARGE");
    }
    return null;
  };

  const startUpload = async () => {
    if (uploading || !hasPending) return;
    const pending = items.filter(
      (item) => item.status === "queued" || item.status === "failed",
    );
    const uploadable: BatchItem[] = [];
    for (const item of pending) {
      const validationError = validateItem(item);
      if (validationError) {
        updateItem(item.uid, { status: "failed", error: validationError });
      } else {
        uploadable.push({ ...item, displayName: item.displayName.trim() });
      }
    }
    if (uploadable.length === 0) return;

    setUploading(true);
    let cursor = 0;
    let succeeded = 0;
    const epoch = batchEpoch.current;
    const worker = async () => {
      while (cursor < uploadable.length) {
        if (batchEpoch.current !== epoch) return;
        const item = uploadable[cursor];
        cursor += 1;
        updateItem(item.uid, {
          displayName: item.displayName,
          status: "uploading",
          error: null,
        });
        const controller = new AbortController();
        activeControllers.current.add(controller);
        try {
          const document = await createIngestionDocument(
            token,
            item.displayName,
            item.file,
            item.idempotencyKey,
            controller.signal,
            item.declaredMaterialKind,
            knowledgeScope,
          );
          if (batchEpoch.current !== epoch) return;
          succeeded += 1;
          updateItem(item.uid, {
            status: "succeeded",
            error: null,
            documentId: document.id,
            versionId: document.latest_version?.id ?? null,
          });
        } catch (reason) {
          if (!controller.signal.aborted && batchEpoch.current === epoch) {
            updateItem(item.uid, {
              status: "failed",
              error: userFacingIngestionError(reason),
            });
          }
        } finally {
          activeControllers.current.delete(controller);
        }
      }
    };

    try {
      await Promise.all(
        Array.from(
          { length: Math.min(MAX_CONCURRENT_UPLOADS, uploadable.length) },
          () => worker(),
        ),
      );
    } finally {
      setUploading(false);
      if (succeeded > 0) onComplete();
    }
  };

  return (
    <Modal
      open={open}
      width={820}
      title="批量上传材料"
      okText={hasPending ? "开始上传" : "上传完成"}
      cancelText="关闭"
      confirmLoading={uploading}
      okButtonProps={{
        disabled: uploading || !hasPending || !capabilities?.upload_enabled,
      }}
      cancelButtonProps={{ disabled: uploading }}
      closable={!uploading}
      maskClosable={false}
      onOk={() => void startUpload()}
      onCancel={() => {
        if (!uploading) onCancel();
      }}
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        message="一次最多 10 份，并发上传 2 份"
        description="先为每份材料选择政策、报告或待分类，可减少后续误归库；默认待分类最稳妥。系统仍会给出机器建议，但不会替代你的选择。上传后自动开始安全处理，不会自动解除隔离。"
        style={{ marginBottom: 16 }}
      />
      <Alert
        type="success"
        showIcon
        message={
          knowledgeScope.kind === "client"
            ? `归属客户：${knowledgeScope.client_display_name ?? "当前客户档案"}`
            : "归属范围：当前环保服务公司"
        }
        description={
          scopeHint ??
          (knowledgeScope.kind === "client"
            ? "归属由客户档案入口带入并锁定；机器分析不会更改客户归属。"
            : "归属由当前入口带入并锁定；机器分析不会更改公司归属。客户专属材料请从客户档案详情上传。")
        }
        style={{ marginBottom: 16 }}
      />
      {!capabilities?.upload_enabled && (
        <Alert
          type="warning"
          showIcon
          message={reasonCopy(capabilities?.disabled_reason_code)}
          style={{ marginBottom: 16 }}
        />
      )}
      <Upload.Dragger
        accept={allowedExtensions.join(",")}
        multiple
        maxCount={MAX_BATCH_FILES}
        fileList={items.map((item) => item.uploadFile)}
        showUploadList={false}
        beforeUpload={() => false}
        disabled={uploading || !capabilities?.upload_enabled}
        onChange={({ fileList }) => {
          const next = fileList.slice(0, MAX_BATCH_FILES);
          setItems((current) => {
            const existing = new Map(current.map((item) => [item.uid, item]));
            return next.flatMap((uploadFile) => {
              const retained = existing.get(uploadFile.uid);
              if (retained) return [{ ...retained, uploadFile }];
              const file = uploadFile.originFileObj;
              if (!file) return [];
              return [
                {
                  uid: uploadFile.uid,
                  uploadFile,
                  file,
                  displayName: defaultDisplayName(file.name),
                  declaredMaterialKind: defaultMaterialKind,
                  idempotencyKey: newIdempotencyKey(),
                  status: "queued" as const,
                  error: null,
                  documentId: null,
                  versionId: null,
                },
              ];
            });
          });
        }}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">点击或拖拽材料到这里</p>
        <p className="ant-upload-hint">
          {allowedExtensions.length > 0
            ? `允许 ${allowedExtensions.map((value) => value.toUpperCase()).join(" / ")}`
            : "正在读取允许格式与上限"}
        </p>
      </Upload.Dragger>

      {items.length > 0 && (
        <List
          style={{ marginTop: 16, maxHeight: "46vh", overflow: "auto" }}
          bordered
          dataSource={items}
          renderItem={(item, index) => (
            <List.Item>
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Typography.Text strong>{index + 1}. {item.file.name}</Typography.Text>
                  <Space>
                    {statusTag(item.status)}
                    <Button
                      type="text"
                      danger
                      aria-label={`移除 ${item.file.name}`}
                      icon={<DeleteOutlined />}
                      disabled={uploading || item.status === "uploading"}
                      onClick={() =>
                        setItems((current) => current.filter((entry) => entry.uid !== item.uid))
                      }
                    />
                  </Space>
                </Space>
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    addonBefore="文档名称"
                    value={item.displayName}
                    maxLength={160}
                    disabled={uploading || item.status === "uploading" || item.status === "succeeded"}
                    onChange={(event) =>
                      updateItem(item.uid, {
                        displayName: event.target.value,
                        idempotencyKey: newIdempotencyKey(),
                        status: item.status === "failed" ? "queued" : item.status,
                        error: null,
                      })
                    }
                  />
                </Space.Compact>
                <Space wrap align="center">
                  <Typography.Text>人工预分类</Typography.Text>
                  <Select<MaterialKind>
                    aria-label={`${item.file.name} 的人工预分类`}
                    value={item.declaredMaterialKind}
                    style={{ width: 180 }}
                    disabled={uploading || item.status === "uploading" || item.status === "succeeded"}
                    options={[
                      { value: "unknown", label: "待分类（默认）" },
                      { value: "policy", label: "政策／法规" },
                      { value: "report", label: "检测／评估报告" },
                    ]}
                    onChange={(declaredMaterialKind) =>
                      updateItem(item.uid, {
                        declaredMaterialKind,
                        idempotencyKey: newIdempotencyKey(),
                        status: item.status === "failed" ? "queued" : item.status,
                        error: null,
                      })
                    }
                  />
                  <Typography.Text type="secondary">
                    这是人工输入；机器分析只会提供建议，之后仍可修改。
                  </Typography.Text>
                </Space>
                <Space wrap>
                  <Typography.Text type="secondary">{formatBytes(item.file.size)}</Typography.Text>
                  {item.error && <Typography.Text type="danger">{item.error}</Typography.Text>}
                </Space>
              </Space>
            </List.Item>
          )}
        />
      )}
    </Modal>
  );
}
