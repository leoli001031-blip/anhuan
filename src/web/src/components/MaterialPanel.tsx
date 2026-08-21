// 材料面板：共享域与客户域通用。上传即真实交互单元（Modal + 文件选择）。
// 列表只用表格 + 状态小圆点，范围列不出现任何技术标识。
import { useCallback, useEffect, useState } from "react";
import { Button, Form, Input, Modal, Table, Typography, Upload, message } from "antd";
import { useApi } from "../adapters";
import type { MaterialItem, MaterialStatus } from "../adapters/types";
import { MATERIAL_STATUS_LABEL } from "../adapters/types";
import ErrorState from "./ErrorState";
import StatusDot, { type StatusTone } from "./StatusDot";
import { formatDateTime } from "./ReportDocument";

const STATUS_TONE: Record<MaterialStatus, StatusTone> = {
  processing: "processing",
  ready: "success",
  blocked: "warning",
  failed: "danger",
};

export default function MaterialPanel({
  scope,
  clientId,
}: {
  scope: "shared" | "client";
  clientId?: string;
}) {
  const api = useApi();
  const [rows, setRows] = useState<MaterialItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [form] = Form.useForm<{ name: string }>();
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    const load =
      scope === "shared" ? api.listSharedMaterials() : api.listClientMaterials(clientId!);
    load
      .then((items) => {
        if (active) setRows(items);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, scope, clientId, nonce]);

  const upload = useCallback(async () => {
    if (!file) return;
    const values = await form.validateFields();
    setUploading(true);
    try {
      await api.uploadMaterial({
        file,
        name: values.name || file.name,
        scope,
        clientId,
      });
      message.success("已上传，正在处理");
      setUploadOpen(false);
      setFile(null);
      form.resetFields();
      setNonce((n) => n + 1);
    } catch {
      message.error("上传失败，请重试");
    } finally {
      setUploading(false);
    }
  }, [api, file, form, scope, clientId]);

  if (error) {
    return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <Button type="primary" onClick={() => setUploadOpen(true)}>
          {scope === "shared" ? "上传共享材料" : "上传指定客户材料"}
        </Button>
      </div>
      <Table<MaterialItem>
        rowKey="id"
        loading={rows === null}
        dataSource={rows ?? []}
        pagination={false}
        locale={{ emptyText: scope === "shared" ? "暂无共享材料" : "暂无指定客户材料" }}
        columns={[
          { title: "名称", dataIndex: "name" },
          {
            title: "状态",
            dataIndex: "status",
            width: 120,
            render: (status: MaterialStatus) => (
              <StatusDot tone={STATUS_TONE[status]} label={MATERIAL_STATUS_LABEL[status]} />
            ),
          },
          { title: "版本数", dataIndex: "versionCount", width: 90 },
          {
            title: "更新时间",
            dataIndex: "updatedAt",
            width: 160,
            render: (iso: string) => (
              <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
            ),
          },
        ]}
      />
      <Modal
        title={scope === "shared" ? "上传共享材料" : "上传指定客户材料"}
        open={uploadOpen}
        onOk={() => void upload()}
        onCancel={() => setUploadOpen(false)}
        confirmLoading={uploading}
        okButtonProps={{ disabled: !file }}
        okText="上传"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="材料名称（留空则使用文件名）">
            <Input placeholder="例如：排污许可证" />
          </Form.Item>
          <Form.Item label="文件" required>
            <Upload
              beforeUpload={(f) => {
                setFile(f);
                return false;
              }}
              maxCount={1}
              onRemove={() => setFile(null)}
            >
              <Button>选择文件</Button>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
