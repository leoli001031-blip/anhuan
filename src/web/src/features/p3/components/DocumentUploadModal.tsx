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
import { useAuth } from '../../../auth/OidcProvider';
import { useSessionAccess } from '../../../adapters';
import { completePendingWrite } from '../../../adapters/pendingWrites';
import { pendingUpload, uploadFileError, validateDocumentReceipt, validateVersionReceipt } from '../uploadWrite';
import { useAsyncContext } from '../../../components/useAsyncContext';

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
  acceptedContentTypes?: readonly string[];
  onCancel: () => void;
  onSuccess: (result: UploadResult) => void;
}

export default function DocumentUploadModal({
  open,
  mode,
  token,
  documentId,
  capabilities,
  acceptedContentTypes,
  onCancel,
  onSuccess,
}: DocumentUploadModalProps) {
  const {user} = useAuth();
  const {session} = useSessionAccess();
  const context = `${session?.enterprise_id ?? ''}:${user?.profile.sub ?? ''}:${documentId ?? ''}:${mode}:${open}`;
  const current = useAsyncContext(context);
  const [form] = Form.useForm<{ display_name: string }>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flight = useRef(false);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) {
      form.resetFields();
      setFileList([]);
      setError(null);
      setSubmitting(false);
      flight.current = false;
    }
    return () => {
      activeRequest.current?.abort();
      activeRequest.current = null;
    };
  }, [form, open, context]);

  const allowedTypes =
    capabilities?.allowed_types.filter(
      (item) => !acceptedContentTypes || acceptedContentTypes.includes(item.content_type),
    ) ?? [];
  const accept = allowedTypes
    .flatMap((item) => [item.content_type, ...item.extensions.map((ext) => ext.toLowerCase())])
    .join(",");

  const close = () => {
    if (flight.current) return;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setSubmitting(false);
    onCancel();
  };

  const submit = async () => {
    if (flight.current || !current()) return;
    const file = fileList[0]?.originFileObj;
    if (!file) {
      setError("请选择一个文件");
      return;
    }
    if (!capabilities?.upload_enabled) {
      setError(reasonCopy(capabilities?.disabled_reason_code));
      return;
    }
    const validation = uploadFileError(file,capabilities,allowedTypes.map(x=>x.content_type));
    if (validation) {
      setError(validation);
      return;
    }

    // Fence before async form validation or file hashing.
    flight.current = true;
    let displayName = "";
    if (mode === "create") {
      try {
        displayName = (await form.validateFields()).display_name.trim();
      } catch {
        if(current())flight.current=false;
        return;
      }
    } else if (!documentId) {
      setError("文档标识缺失");
      flight.current=false;
      return;
    }

    if(!current())return;
    const controller = new AbortController();
    activeRequest.current?.abort();
    activeRequest.current = controller;
    setSubmitting(true);
    setError(null);
    try {
      const scope = {kind:'service_provider' as const,client_account_id:null};
      const identity = await pendingUpload(session?.enterprise_id ?? '',user?.profile.sub ?? '',file,
        mode==='create' ? {displayName,scope} : {documentId:documentId!});
      if(!current() || controller.signal.aborted)return;
      if (mode === "create") {
        const document = await createIngestionDocument(
          token,
          displayName,
          file,
          identity.requestId,
          controller.signal,
        );
        validateDocumentReceipt(document,file,scope);
        completePendingWrite(identity);
        if (controller.signal.aborted || activeRequest.current !== controller) return;
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
          identity.requestId,
          controller.signal,
        );
        validateVersionReceipt(version,file,documentId!);
        completePendingWrite(identity);
        if (controller.signal.aborted || activeRequest.current !== controller) return;
        message.success("新版本已进入隔离区");
        onSuccess({ documentId: version.document_id, versionId: version.id });
      }
    } catch (reason) {
      if (!controller.signal.aborted && activeRequest.current === controller) {
        setError(userFacingIngestionError(reason));
      }
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null;
        flight.current = false;
        setSubmitting(false);
      }
    }
  };

  return (
    <Modal
      open={open}
      title={mode === "create" ? "新建文档" : "上传新版本"}
      okText="上传到隔离区"
      cancelText="取消"
      confirmLoading={submitting}
      okButtonProps={{ disabled: !capabilities?.upload_enabled || allowedTypes.length === 0 }}
      closable={!submitting}
      maskClosable={!submitting}
      keyboard={!submitting}
      cancelButtonProps={{disabled:submitting}}
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
            ? allowedTypes
                .map(
                  (item) =>
                    `${item.extensions.map((ext) => ext.toUpperCase()).join("/")} ${formatBytes(item.max_file_bytes)}`,
                )
                .join("；") || "当前环境未开放所需文件格式"
            : "正在读取允许格式与上限"}
        </p>
      </Upload.Dragger>
    </Modal>
  );
}
