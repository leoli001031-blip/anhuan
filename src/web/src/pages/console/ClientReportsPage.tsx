// 运营台 · 客户报告列表：版本状态一眼可读；新建报告为幂等创建（request_id 由前端生成）。
import { useEffect, useRef, useState } from "react";
import { Button, Table, Typography, message } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import { errorKind } from "../../adapters/errors";
import type { ProviderReportSummaryV1, ReportStatus } from "../../adapters/types";
import { REPORT_STATUS_LABEL } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import ClientShell from "./ClientShell";

const REPORT_STATUS_TONE: Record<ReportStatus, StatusTone> = {
  empty: "neutral",
  queued: "processing",
  generating: "processing",
  draft: "neutral",
  review_pending: "warning",
  changes_requested: "danger",
  approved: "success",
  published: "success",
  superseded: "neutral",
  withdrawn: "neutral",
  failed: "danger",
};

export default function ClientReportsPage() {
  const { clientId = "" } = useParams();
  const api = useApi();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ProviderReportSummaryV1[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [creating, setCreating] = useState(false);
  const createRequestId = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .listClientReports(clientId)
      .then((items) => {
        if (active) setRows(items);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, clientId, nonce]);

  const create = async () => {
    setCreating(true);
    if (!createRequestId.current) createRequestId.current = crypto.randomUUID();
    try {
      const report = await api.createReport(clientId, createRequestId.current);
      createRequestId.current = null;
      message.success("报告已创建，请生成首个版本");
      navigate(`/console/clients/${clientId}/reports/${report.report_id}`);
    } catch (e) {
      if (errorKind(e) === "conflict") {
        createRequestId.current = null;
        message.warning("请求冲突，已为你刷新");
        setNonce((n) => n + 1);
      } else {
        message.error("创建失败，请重试");
      }
    } finally {
      setCreating(false);
    }
  };

  return (
    <ClientShell clientId={clientId}>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
            <Button type="primary" loading={creating} onClick={() => void create()}>
              新建报告
            </Button>
          </div>
          <Table<ProviderReportSummaryV1>
            rowKey="report_id"
            loading={rows === null}
            dataSource={rows ?? []}
            pagination={false}
            locale={{ emptyText: "暂无报告，点击右上角新建" }}
            columns={[
              { title: "标题", dataIndex: "title" },
              {
                title: "版本",
                dataIndex: "version_number",
                width: 90,
                render: (n: number) => (n > 0 ? `第 ${n} 版` : "—"),
              },
              {
                title: "状态",
                dataIndex: "current_status",
                width: 140,
                render: (status: ReportStatus) => (
                  <StatusDot
                    tone={REPORT_STATUS_TONE[status]}
                    label={REPORT_STATUS_LABEL[status]}
                  />
                ),
              },
              {
                title: "更新时间",
                dataIndex: "updated_at",
                width: 170,
                render: (iso: string) => (
                  <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
                ),
              },
              {
                title: "操作",
                key: "actions",
                width: 110,
                render: (_, row) => (
                  <Link to={`/console/clients/${clientId}/reports/${row.report_id}`}>
                    打开工作台
                  </Link>
                ),
              },
            ]}
          />
        </>
      )}
    </ClientShell>
  );
}
