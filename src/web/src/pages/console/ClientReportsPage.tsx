// 运营台 · 客户报告列表：版本状态一眼可读；新建报告为幂等创建（request_id 由前端生成）。
// 已归档报告默认不展示；勾选“显示已归档”后拉取，仅提供恢复入口，不再进入工作台。
// 列表数据、待归档目标与动作全部绑定客户上下文（epoch）：切换客户立即清空旧列表/
// 弹窗/目标并使旧行不可操作；提交前校验目标仍属当前上下文，完成回调做代次与动作
// 所有权双重检查。<768px 切换为列表形态：不逐字换行、不依赖横向拖动。
import { useEffect, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import { errorKind } from "../../adapters/errors";
import type { ProviderReportSummaryV1, ReportStatus } from "../../adapters/types";
import { REPORT_STATUS_LABEL } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import { useAsyncContext } from "../../components/useAsyncContext";
import ClientShell from "./ClientShell";
import { useNarrow } from "./useNarrow";

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
  const isCurrent = useAsyncContext(clientId);
  const createSeq = useRef(0);
  const createPending = useRef(false);
  const narrow = useNarrow();
  const [rows, setRows] = useState<ProviderReportSummaryV1[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [creating, setCreating] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  // 归档动作上下文：待归档报告与其理由输入；动作完成后整表刷新。
  const [archiveTarget, setArchiveTarget] = useState<ProviderReportSummaryV1 | null>(null);
  const [archiveReason, setArchiveReason] = useState("");
  const [actionReportId, setActionReportId] = useState<string | null>(null);
  const createRequestId = useRef<string | null>(null);

  // 上下文代次：仅随客户切换/卸载递增（列表刷新/筛选不递增），旧列表响应、
  // 旧目标与旧动作回调据此作废。
  const contextEpoch = useRef(0);
  // 动作序号：每个动作领取自己的 loading 槽位；旧动作的 finally 不得清新动作的。
  const actionSeq = useRef(0);
  // 归档弹窗绑定代次：提交前确认目标仍属于当前客户上下文。
  const targetEpoch = useRef(0);

  useEffect(() => {
    ++contextEpoch.current;
    return () => {
      ++contextEpoch.current;
    };
  }, [clientId]);

  // 切客户立即清空客户绑定的瞬时状态：旧列表行消失（不可再点归档/恢复）、
  // 归档弹窗关闭、待确认目标与理由作废、幂等 request ID 不跨客户复用。
  useEffect(() => {
    setRows(null);
    setArchiveTarget(null);
    setArchiveReason("");
    setActionReportId(null);
    setCreating(false);
    createPending.current = false;
    createRequestId.current = null;
  }, [clientId]);

  useEffect(() => {
    const epoch = contextEpoch.current;
    let active = true;
    setError(null);
    api
      .listClientReports(clientId, { includeArchived: showArchived })
      .then((items) => {
        if (active && contextEpoch.current === epoch) setRows(items);
      })
      .catch((e) => {
        if (active && contextEpoch.current === epoch) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, clientId, nonce, showArchived]);

  const create = async () => {
    if (!isCurrent() || createPending.current) return;
    createPending.current = true;
    if (!createRequestId.current) createRequestId.current = crypto.randomUUID();
    const requestId = createRequestId.current;
    const seq = ++createSeq.current;
    const epoch = contextEpoch.current;
    setCreating(true);
    try {
      const report = await api.createReport(clientId, requestId);
      // 旧客户的迟到创建不得导航走当前页面，也不得清新上下文的 request ID。
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      createRequestId.current = null;
      message.success("报告已创建，请生成首个版本");
      navigate(`/console/clients/${clientId}/reports/${report.report_id}`);
    } catch (e) {
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      if (errorKind(e) === "conflict") {
        createRequestId.current = null;
        message.warning("请求冲突，已为你刷新");
        setNonce((n) => n + 1);
      } else {
        message.error("创建失败，请重试");
      }
    } finally {
      if (isCurrent() && createSeq.current === seq) {
        createPending.current = false;
        setCreating(false);
      }
    }
  };

  const submitArchive = async () => {
    if (!isCurrent() || !archiveTarget) return;
    // 提交前校验：弹窗目标绑定时的客户上下文必须仍是当前上下文，否则直接丢弃，
    // 不发起任何写请求（切客户时弹窗已被清理；此处防御弹窗残留的极端情况）。
    if (contextEpoch.current !== targetEpoch.current) {
      setArchiveTarget(null);
      setArchiveReason("");
      return;
    }
    const target = archiveTarget;
    const reason = archiveReason.trim();
    const seq = ++actionSeq.current;
    const epoch = contextEpoch.current;
    setActionReportId(target.report_id);
    try {
      await api.archiveReport(target.report_id, reason.length > 0 ? reason : undefined);
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      message.success("报告已归档，可在“显示已归档”中恢复");
      setArchiveTarget(null);
      setArchiveReason("");
      setNonce((n) => n + 1);
    } catch (e) {
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      if (errorKind(e) === "conflict") {
        message.error("报告存在进行中的生成/审核/发布，不能归档");
      } else {
        message.error("归档失败，请重试");
      }
    } finally {
      if (isCurrent() && actionSeq.current === seq) setActionReportId(null);
    }
  };

  const restore = async (row: ProviderReportSummaryV1) => {
    if (!isCurrent()) return;
    const seq = ++actionSeq.current;
    const epoch = contextEpoch.current;
    setActionReportId(row.report_id);
    try {
      await api.unarchiveReport(row.report_id);
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      message.success("报告已恢复，可继续正常流程");
      setNonce((n) => n + 1);
    } catch {
      if (!isCurrent() || contextEpoch.current !== epoch) return;
      message.error("恢复失败，请重试");
    } finally {
      if (isCurrent() && actionSeq.current === seq) setActionReportId(null);
    }
  };

  const openArchive = (row: ProviderReportSummaryV1) => {
    if (!isCurrent()) return;
    targetEpoch.current = contextEpoch.current;
    setArchiveReason("");
    setArchiveTarget(row);
  };

  const rowActions = (row: ProviderReportSummaryV1) =>
    row.archived_at ? (
      <Popconfirm
        title="恢复该报告？"
        description="恢复后可继续生成、审核与发布流程。"
        okText="恢复"
        cancelText="取消"
        onConfirm={() => void restore(row)}
      >
        <Button
          type="link"
          size="small"
          loading={actionReportId === row.report_id}
          disabled={actionReportId !== null && actionReportId !== row.report_id}
        >
          恢复
        </Button>
      </Popconfirm>
    ) : (
      <>
        <Link to={`/console/clients/${clientId}/reports/${row.report_id}`}>打开工作台</Link>
        <Button
          type="link"
          size="small"
          danger
          loading={actionReportId === row.report_id}
          disabled={actionReportId !== null && actionReportId !== row.report_id}
          onClick={() => openArchive(row)}
        >
          归档
        </Button>
      </>
    );

  const statusCell = (status: ReportStatus, archived: string | null) =>
    archived ? (
      <Tag color="default">已归档</Tag>
    ) : (
      <StatusDot
        tone={REPORT_STATUS_TONE[status]}
        label={REPORT_STATUS_LABEL[status]}
      />
    );

  return (
    <ClientShell clientId={clientId}>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      ) : (
        <>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 12,
            }}
          >
            <Checkbox
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            >
              显示已归档
            </Checkbox>
            <Button type="primary" loading={creating} onClick={() => void create()}>
              新建报告
            </Button>
          </div>
          {narrow ? (
            rows === null ? (
              <Spin style={{ display: "block", margin: "48px auto" }} />
            ) : rows.length === 0 ? (
              <Typography.Text type="secondary">
                {showArchived ? "暂无报告" : "暂无报告，点击右上角新建"}
              </Typography.Text>
            ) : (
              <div>
                {rows.map((r) => (
                  <div key={r.report_id} className="client-mobile-item">
                    {r.archived_at ? (
                      <Typography.Text style={{ fontSize: 15 }}>{r.title}</Typography.Text>
                    ) : (
                      <Link
                        to={`/console/clients/${clientId}/reports/${r.report_id}`}
                        style={{ fontSize: 15 }}
                      >
                        {r.title}
                        {r.version_number > 0 ? ` · 第 ${r.version_number} 版` : ""}
                      </Link>
                    )}
                    <div className="client-mobile-meta">
                      {statusCell(r.current_status, r.archived_at)}
                      <span style={{ marginLeft: 8 }}>
                        更新于 {formatDateTime(r.updated_at)}
                      </span>
                    </div>
                    <div className="client-mobile-actions">{rowActions(r)}</div>
                  </div>
                ))}
              </div>
            )
          ) : (
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
                  render: (status: ReportStatus, row) =>
                    statusCell(status, row.archived_at),
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
                  width: 190,
                  render: (_, row) => rowActions(row),
                },
              ]}
            />
          )}
          <Modal
            title="归档报告"
            open={archiveTarget !== null}
            okText="归档"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onOk={() => void submitArchive()}
            onCancel={() => {
              setArchiveTarget(null);
              setArchiveReason("");
            }}
            destroyOnClose
          >
            <Typography.Paragraph type="secondary">
              归档后报告默认从列表隐藏，客户侧不可见，不能生成、审核或发布；可在“显示已归档”中恢复。归档人、时间与原因写入审计记录。
            </Typography.Paragraph>
            <Input.TextArea
              value={archiveReason}
              onChange={(e) => setArchiveReason(e.target.value)}
              maxLength={500}
              showCount
              rows={3}
              placeholder="归档原因（选填，≤500 字，写入审计）"
            />
          </Modal>
        </>
      )}
    </ClientShell>
  );
}
