import { useEffect, useRef, useState } from "react";
import { Alert, Form, Input, Modal, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import {
  createIngestionDocument,
  uploadDocumentVersion,
  userFacingIngestionError,
} from "../ingestionApi";
import { formatBytes, reasonCopy } from "../reasonCopy";
import type { IngestionCapabilities } from "../types";

interface UploadResult {
  documentId: string;
  versionId: string | null;
}

interface DocumentUploadModalProps {
  open: boolean;
  mode: "create" | "version";
  token: string | null;
  documentId?: string;
  capabilities: IngestionCapabilities | null;
  onCancel: () => void;
  onSuccess: (result: UploadResult) => void;
}

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function extensionOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index < 0 ? "" : filename.slice(index).toLowerCase();
}

export default function DocumentUploadModal({
  open,
  mode,
  token,
  documentId,
  capabilities,
  onCancel,
  onSuccess,
}: DocumentUploadModalProps) {
  const [form] = Form.useForm<{ display_name: string }>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(newIdempotencyKey());
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setFileList([]);
      setError(null);
      idempotencyKey.current = newIdempotencyKey();
    }
    return () => activeRequest.current?.abort();
  }, [form, open]);

  const allowedExtensions =
    capabilities?.allowed_types.flatMap((item) => item.extensions.map((ext) => ext.toLowerCase())) ?? [];
  const accept = allowedExtensions.join(",");

  const close = () => {
    activeRequest.current?.abort();
    activeRequest.current = null;
    setSubmitting(false);
    onCancel();
  };

  const submit = async () => {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      setError("请选择一个文件");
      return;
    }
    if (!capabilities?.upload_enabled) {
      setError(reasonCopy(capabilities?.disabled_reason_code));
      return;
    }
    const extension = extensionOf(file.name);
    const formatCapability = capabilities.allowed_types.find((item) =>
      item.extensions.map((ext) => ext.toLowerCase()).includes(extension),
    );
    if (!formatCapability) {
      setError(reasonCopy("FILE_TYPE_NOT_ALLOWED"));
      return;
    }
    if (
      file.size > formatCapability.max_file_bytes ||
      file.size > capabilities.limits.max_file_bytes
    ) {
      setError(reasonCopy("FILE_TOO_LARGE"));
      return;
    }

    let displayName = "";
    if (mode === "create") {
      try {
        displayName = (await form.validateFields()).display_name.trim();
      } catch {
        return;
      }
    } else if (!documentId) {
      setError("文档标识缺失");
      return;
    }

    const controller = new AbortController();
    activeRequest.current?.abort();
    activeRequest.current = controller;
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "create") {
        const document = await createIngestionDocument(
          token,
          displayName,
          file,
          idempotencyKey.current,
          controller.signal,
        );
        message.success("文件已进入隔离区");
        onSuccess({
          documentId: document.id,
          versionId: document.latest_version?.id ?? null,
        });
      } else {
        const version = await uploadDocumentVersion(
          token,
          documentId as string,
          file,
          idempotencyKey.current,
          controller.signal,
        );
        message.success("新版本已进入隔离区");
        onSuccess({ documentId: version.document_id, versionId: version.id });
      }
      idempotencyKey.current = newIdempotencyKey();
    } catch (reason) {
      if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={mode === "create" ? "新建文档" : "上传新版本"}
      okText="上传到隔离区"
      cancelText="取消"
      confirmLoading={submitting}
      okButtonProps={{ disabled: !capabilities?.upload_enabled }}
      closable={!submitting}
      maskClosable={!submitting}
      onOk={() => void submit()}
      onCancel={close}
      destroyOnHidden
    >
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
      )}
      {mode === "create" && (
        <Form form={form} layout="vertical">
          <Form.Item
            name="display_name"
            label="文档名称"
            rules={[
              { required: true, whitespace: true, message: "请输入文档名称" },
              { max: 160, message: "文档名称不能超过 160 个字符" },
            ]}
          >
            <Input placeholder="例如：季度检查记录" autoComplete="off" />
          </Form.Item>
        </Form>
      )}
      <Upload.Dragger
        accept={accept}
        maxCount={1}
        fileList={fileList}
        beforeUpload={() => false}
        onChange={({ fileList: next }) => {
          setError(null);
          setFileList(next.slice(-1));
        }}
        onRemove={() => {
          setFileList([]);
          return true;
        }}
        disabled={submitting || !capabilities?.upload_enabled}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽一个文件到这里</p>
        <p className="ant-upload-hint">
          {capabilities
            ? capabilities.allowed_types
                .map(
                  (item) =>
                    `${item.extensions.map((ext) => ext.toUpperCase()).join("/")} ${formatBytes(item.max_file_bytes)}`,
                )
                .join("；")
            : "正在读取允许格式与上限"}
        </p>
      </Upload.Dragger>
    </Modal>
  );
}
