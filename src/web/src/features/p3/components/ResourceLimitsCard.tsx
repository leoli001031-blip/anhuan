import { Alert, Card, Descriptions, Space, Tag, Typography } from "antd";
import { formatBytes, formatDateTime, reasonCopy, statusColor } from "../reasonCopy";
import type { IngestionCapabilities } from "../types";

interface ResourceLimitsCardProps {
  capabilities: IngestionCapabilities | null;
  loading?: boolean;
}

function scannerCopy(state: IngestionCapabilities["scanner"]["state"]): string {
  if (state === "ready") return "本地扫描可用";
  if (state === "degraded") return "本地扫描降级";
  return "本地扫描不可用";
}

export default function ResourceLimitsCard({
  capabilities,
  loading = false,
}: ResourceLimitsCardProps) {
  return (
    <Card size="small" title="受控导入能力" loading={loading}>
      {!capabilities ? (
        <Typography.Text type="secondary">尚未加载资源限制</Typography.Text>
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div>
            <Typography.Text strong>各格式单文件上限</Typography.Text>
            <Space size={[4, 4]} wrap style={{ display: "flex", marginTop: 8 }}>
              {capabilities.allowed_types.map((item) => (
                <Tag key={item.content_type}>
                  {item.extensions.map((ext) => ext.toUpperCase()).join(" / ")} ≤{" "}
                  {formatBytes(item.max_file_bytes)}
                </Tag>
              ))}
            </Space>
          </div>
          {!capabilities.upload_enabled && (
            <Alert
              type="warning"
              showIcon
              message="当前暂停上传"
              description={reasonCopy(capabilities.disabled_reason_code)}
            />
          )}
          {capabilities.scanner.state !== "ready" && (
            <Alert
              type="warning"
              showIcon
              message={scannerCopy(capabilities.scanner.state)}
              description="新文件会继续留在隔离区，不会降级放行。"
            />
          )}
          <Descriptions
            size="small"
            column={{ xs: 1, sm: 2, lg: 4 }}
            items={[
              {
                key: "scanner",
                label: "安全扫描",
                children: (
                  <Tag color={statusColor(capabilities.scanner.state)}>
                    {scannerCopy(capabilities.scanner.state)}
                  </Tag>
                ),
              },
              {
                key: "max-file",
                label: "绝对文件上限",
                children: formatBytes(capabilities.limits.max_file_bytes),
              },
              {
                key: "max-version",
                label: "版本上限",
                children: `${capabilities.limits.max_versions_per_document} 个/文档`,
              },
              {
                key: "checked",
                label: "扫描状态更新时间",
                children: formatDateTime(capabilities.scanner.last_checked_at),
              },
              {
                key: "page-limits",
                label: "文本分页上限",
                children: `PDF ${capabilities.limits.max_pdf_pages} 页；DOCX ${capabilities.limits.max_docx_pages} 页`,
              },
              {
                key: "sheet-limits",
                label: "表格预览上限",
                children: `${capabilities.limits.max_xlsx_sheets} 个工作表；每表 ${capabilities.limits.max_xlsx_rows_per_sheet} 行 × ${capabilities.limits.max_xlsx_columns} 列`,
              },
              {
                key: "image-limit",
                label: "图片像素上限",
                children: capabilities.limits.max_image_pixels.toLocaleString("zh-CN"),
              },
            ]}
          />
        </Space>
      )}
    </Card>
  );
}
