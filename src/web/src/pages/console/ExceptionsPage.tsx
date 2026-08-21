// 运营台 · 异常中心：只呈现可行动的业务错误，永不显示原始日志、堆栈或技术标识。
// 冻结合同 v1 尚未定义异常列表端点：HTTP 模式下后端明确返回不可用，本页呈现专属状态。
import { useEffect, useState } from "react";
import { Table, Typography } from "antd";
import { useApi } from "../../adapters";
import type { ExceptionItem } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";

export default function ExceptionsPage() {
  const api = useApi();
  const [rows, setRows] = useState<ExceptionItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .listExceptions()
      .then((items) => {
        if (active) setRows(items);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, nonce]);

  if (error) {
    // 503 → 「服务暂时不可用」；异常中心语义下即「尚未接入」，由统一错误态承接。
    return (
      <div style={{ maxWidth: 960 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          异常中心
        </Typography.Title>
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 960 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        异常中心
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        这里只列出需要人工处理的问题，处理完成后会自动消失。
      </Typography.Paragraph>
      <Table<ExceptionItem>
        rowKey="id"
        loading={rows === null}
        dataSource={rows ?? []}
        pagination={false}
        locale={{ emptyText: "当前没有需要处理的异常" }}
        columns={[
          {
            title: "时间",
            dataIndex: "occurredAt",
            width: 160,
            render: (iso: string) => (
              <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
            ),
          },
          {
            title: "客户",
            dataIndex: "clientName",
            width: 200,
            render: (name: string | null) => name ?? "共享域",
          },
          { title: "问题", dataIndex: "kind", width: 140 },
          {
            title: "说明与建议",
            key: "detail",
            render: (_, row) => (
              <span>
                {row.message}
                <Typography.Text type="secondary"> 建议：{row.actionHint}</Typography.Text>
              </span>
            ),
          },
        ]}
      />
    </div>
  );
}
