// 运营台 · 客户整改聚合（/console/clients/:clientId/rectification）。
// 先获取客户服务事项，再逐项读取详情并强制校验 client_account_id；任何失配都 fail-closed。
import { useEffect, useMemo, useState } from "react";
import { Empty, Spin, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useParams } from "react-router-dom";
import { useAuth } from "../../auth/OidcProvider";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import {
  getClientServiceCase,
  listClientServiceCases,
} from "../../p2Api";
import ClientShell from "./ClientShell";
import { useNarrow } from "./useNarrow";

interface RectificationRow {
  key: string;
  title: string;
  severity: string;
  status: string;
  dueAt: string | null;
  serviceTitle: string;
}

interface ClientRectificationState {
  contextId: string;
  rows: RectificationRow[] | null;
  error: unknown;
}

const SEVERITY_LABEL: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

const SEVERITY_TONE: Record<string, StatusTone> = {
  critical: "danger",
  high: "danger",
  medium: "warning",
  low: "neutral",
};

const FINDING_STATUS_LABEL: Record<string, string> = {
  open: "待处理",
  rectifying: "整改中",
  in_rectification: "整改中",
  submitted: "已提交",
  reviewing: "待复核",
  in_review: "待复核",
  pending_review: "待复核",
  passed: "已通过",
  rejected: "已退回",
  closed: "已关闭",
};

function findingStatusTone(status: string): StatusTone {
  if (["passed", "closed", "completed"].includes(status)) return "success";
  if (["rejected", "overdue"].includes(status)) return "danger";
  if (["submitted", "reviewing", "in_review", "pending_review"].includes(status)) {
    return "warning";
  }
  if (["rectifying", "in_rectification"].includes(status)) return "processing";
  return "neutral";
}

function isFindingOverdue(row: RectificationRow): boolean {
  if (!row.dueAt || ["passed", "closed", "completed", "cancelled"].includes(row.status)) {
    return false;
  }
  const due = new Date(row.dueAt);
  return !Number.isNaN(due.getTime()) && due.getTime() < Date.now();
}

function dueLabel(row: RectificationRow): string {
  if (!row.dueAt) return "未设期限";
  return `${formatDateTime(row.dueAt)}${isFindingOverdue(row) ? " · 已逾期" : ""}`;
}

export default function ClientRectificationPage() {
  const { clientId = "" } = useParams();
  const { getAccessToken } = useAuth();
  const narrow = useNarrow();
  const [rectificationState, setRectificationState] = useState<ClientRectificationState>(() => ({
    contextId: clientId,
    rows: null,
    error: null,
  }));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    const requestClientId = clientId;
    setRectificationState({ contextId: requestClientId, rows: null, error: null });

    const load = async () => {
      const token = getAccessToken();
      const collection = await listClientServiceCases(token, requestClientId);
      const details = await Promise.all(
        collection.items.map((item) =>
          getClientServiceCase(token, requestClientId, item.id),
        ),
      );
      const nextRows = details.flatMap((serviceCase, caseIndex) =>
        (serviceCase.findings ?? []).map((finding, findingIndex) => ({
          key: `${caseIndex}-${findingIndex}`,
          title: finding.title,
          severity: finding.severity,
          status: finding.status,
          dueAt: finding.due_at,
          serviceTitle: serviceCase.title,
        })),
      );
      nextRows.sort((left, right) => {
        if (!left.dueAt) return 1;
        if (!right.dueAt) return -1;
        return left.dueAt.localeCompare(right.dueAt);
      });
      if (active) {
        setRectificationState({
          contextId: requestClientId,
          rows: nextRows,
          error: null,
        });
      }
    };

    load().catch((reason) => {
      if (active) {
        setRectificationState({
          contextId: requestClientId,
          rows: null,
          error: reason,
        });
      }
    });
    return () => {
      active = false;
    };
  }, [clientId, getAccessToken, nonce]);

  const inCurrentContext = rectificationState.contextId === clientId;
  const rows = inCurrentContext ? rectificationState.rows : null;
  const error = inCurrentContext ? rectificationState.error : null;
  const overdueCount = useMemo(
    () => (rows ?? []).filter(isFindingOverdue).length,
    [rows],
  );

  const columns: TableColumnsType<RectificationRow> = [
    {
      title: "整改问题",
      dataIndex: "title",
      key: "title",
      render: (value: string) => <Typography.Text strong>{value}</Typography.Text>,
    },
    {
      title: "所属服务",
      dataIndex: "serviceTitle",
      key: "serviceTitle",
      width: 240,
    },
    {
      title: "严重度",
      dataIndex: "severity",
      key: "severity",
      width: 110,
      render: (value: string) => (
        <StatusDot
          tone={SEVERITY_TONE[value] ?? "neutral"}
          label={SEVERITY_LABEL[value] ?? "待确认"}
        />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: string) => (
        <StatusDot
          tone={findingStatusTone(value)}
          label={FINDING_STATUS_LABEL[value] ?? "状态待确认"}
        />
      ),
    },
    {
      title: "期限",
      key: "dueAt",
      width: 190,
      render: (_, row) => (
        <Typography.Text type={isFindingOverdue(row) ? "danger" : "secondary"}>
          {dueLabel(row)}
        </Typography.Text>
      ),
    },
  ];

  return (
    <ClientShell clientId={clientId}>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((value) => value + 1)} />
      ) : rows === null ? (
        <Spin style={{ display: "block", margin: "48px auto" }} />
      ) : (
        <div className="client-rectification">
          <div className="client-rectification__summary">
            <Typography.Text>共 {rows.length} 项整改</Typography.Text>
            <Typography.Text type={overdueCount > 0 ? "danger" : "secondary"}>
              {overdueCount > 0 ? `${overdueCount} 项已逾期` : "无逾期项"}
            </Typography.Text>
          </div>

          {rows.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前服务事项暂无整改问题" />
          ) : narrow ? (
            <div className="client-rectification__list">
              {rows.map((row) => (
                <article key={row.key} className="client-rectification__item">
                  <Typography.Text strong>{row.title}</Typography.Text>
                  <Typography.Text type="secondary">所属服务：{row.serviceTitle}</Typography.Text>
                  <div className="client-rectification__meta">
                    <StatusDot
                      tone={SEVERITY_TONE[row.severity] ?? "neutral"}
                      label={SEVERITY_LABEL[row.severity] ?? "待确认"}
                    />
                    <StatusDot
                      tone={findingStatusTone(row.status)}
                      label={FINDING_STATUS_LABEL[row.status] ?? "状态待确认"}
                    />
                  </div>
                  <Typography.Text type={isFindingOverdue(row) ? "danger" : "secondary"}>
                    {dueLabel(row)}
                  </Typography.Text>
                </article>
              ))}
            </div>
          ) : (
            <Table<RectificationRow>
              className="client-rectification__table"
              columns={columns}
              dataSource={rows}
              pagination={false}
              rowKey="key"
            />
          )}
        </div>
      )}
    </ClientShell>
  );
}
