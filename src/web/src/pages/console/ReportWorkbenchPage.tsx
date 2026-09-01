// 运营台 · 报告工作台（/console/clients/:clientId/reports/:reportId）
// ≥1280px 左文档右审核；<1280px 正文在上、审核区在下，全部操作保持可见。
// 退回必须留下原因，撤回保留二次确认；409 映射为「状态已变化」并自动刷新。
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Checkbox, Input, Modal, Spin, Typography, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useApi, useSessionAccess } from "../../adapters";
import {
  saveHtmlReportArtifact,
  type TransitionAction,
  type TransitionEvidence,
} from "../../adapters/AnalysisReportApi";
import { ApiError, errorKind } from "../../adapters/errors";
import type {
  ReportStatus,
  ReviewChecklistV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "../../adapters/types";
import { REPORT_STATUS_LABEL } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import ReportDocument, { formatDateTime } from "../../components/ReportDocument";
import StatusDot, { type StatusTone } from "../../components/StatusDot";

const STATUS_TONE: Record<ReportStatus, StatusTone> = {
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

const REVIEW_CHECKLIST: ReadonlyArray<{
  key: keyof ReviewChecklistV1;
  label: string;
}> = [
  { key: "citation_traceable", label: "引用证据可溯源" },
  { key: "risks_complete", label: "风险与缺口表述完整" },
  { key: "usage_boundary", label: "使用边界已包含" },
];

const REVIEW_ACTION_LABEL = {
  submit: "提交审核",
  return: "退回",
  approve: "批准",
} as const;

const RECOVERABLE_GENERATION_FAILURES = new Set([
  "REPORT_QUEUE_DISPATCH_FAILED",
  "REPORT_QUEUE_STATUS_UNAVAILABLE",
  "REPORT_GENERATION_RETRIES_EXHAUSTED",
  "REPORT_WORKER_GENERATION_DISABLED",
  "REPORT_ACTOR_REVOKED",
]);

function generationFailureCanResume(reason: string | null): boolean {
  return reason !== null && RECOVERABLE_GENERATION_FAILURES.has(reason);
}

function emptyReviewChecklist(): ReviewChecklistV1 {
  return {
    citation_traceable: false,
    risks_complete: false,
    usage_boundary: false,
  };
}

export default function ReportWorkbenchPage() {
  const { reportId = "", clientId = "" } = useParams();
  const api = useApi();
  const { session } = useSessionAccess();
  const capabilities = new Set(session?.capabilities ?? []);
  const generateRequestId = useRef<string | null>(null);
  const detailEpoch = useRef(0);
  const workbenchEpoch = useRef(0);

  const [versions, setVersions] = useState<VersionHistoryItemV1[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VersionDetailV1 | null>(null);
  const [detailError, setDetailError] = useState<unknown>(null);
  const [checked, setChecked] = useState<ReviewChecklistV1>(emptyReviewChecklist);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnComment, setReturnComment] = useState("");
  const [job, setJob] = useState<{ jobId: string; status: string } | null>(null);
  const [jobPollFailed, setJobPollFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [nonce, setNonce] = useState(0);
  // 绑定证明：reportId 必须先出现在该客户的报告列表里，否则禁止读版本与执行动作。
  const [bound, setBound] = useState(false);

  // 生成任务与幂等 request ID 按企业、客户、报告隔离，响应丢失后仍可恢复。
  const storageScope = `${session?.enterprise_id ?? "unbound"}:${clientId}:${reportId}`;
  const jobStorageKey = `ar-job:${storageScope}`;
  const requestStorageKey = `ar-generate-request:${storageScope}`;

  // 路由客户/报告变化：先清旧状态（版本/选中/详情/任务/请求ID），不残留上一上下文。
  useEffect(() => {
    workbenchEpoch.current += 1;
    setVersions(null);
    setListError(null);
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    setChecked(emptyReviewChecklist());
    setReturnOpen(false);
    setReturnComment("");
    setJobPollFailed(false);
    setBound(false);
    generateRequestId.current = sessionStorage.getItem(requestStorageKey);
    detailEpoch.current += 1;
    const saved = sessionStorage.getItem(jobStorageKey);
    setJob(saved ? { jobId: saved, status: "generating" } : null);
  }, [jobStorageKey, requestStorageKey]);

  // 审核勾选只属于当前选中版本；同一版本的详情刷新不清空，
  // 但切换版本时必须防止上一版的本地勾选被带入新审批请求。
  useEffect(() => {
    setDetail(null);
    setChecked(emptyReviewChecklist());
    setReturnOpen(false);
    setReturnComment("");
  }, [selectedId]);

  // 归属证明：listClientReports(clientId) 命中 reportId 才允许后续读取与动作
  useEffect(() => {
    if (bound) return;
    let active = true;
    api
      .listClientReports(clientId)
      .then((reports) => {
        if (!active) return;
        if (reports.some((r) => r.report_id === reportId)) {
          setBound(true);
        } else {
          setListError(new ApiError(404, "REPORT_NOT_FOUND", false));
        }
      })
      .catch((e) => {
        if (active) setListError(e);
      });
    return () => {
      active = false;
    };
  }, [api, clientId, reportId, bound]);

  // 版本历史（绑定后才读取）
  useEffect(() => {
    if (!bound) return;
    let active = true;
    setListError(null);
    api
      .listVersions(reportId)
      .then((items) => {
        if (!active) return;
        setVersions(items);
        setSelectedId((prev) =>
          prev && items.some((v) => v.version_id === prev)
            ? prev
            : (items[items.length - 1]?.version_id ?? null),
        );
      })
      .catch((e) => {
        if (active) setListError(e);
      });
    return () => {
      active = false;
    };
  }, [api, reportId, nonce, bound]);

  // 选中版本的草稿/审核详情。刷新不得清空已勾审核清单；epoch 拒绝迟到响应。
  useEffect(() => {
    if (!selectedId || !bound) {
      setDetail(null);
      return;
    }
    let active = true;
    const at = ++detailEpoch.current;
    setDetailError(null);
    api
      .getVersion(selectedId)
      .then((d) => {
        if (active && at === detailEpoch.current) setDetail(d);
      })
      .catch((e) => {
        if (active && at === detailEpoch.current) setDetailError(e);
      });
    return () => {
      active = false;
    };
  }, [api, selectedId, nonce, bound]);

  // 生成进度轮询：终态即停并校验版本归属；短暂失败保留任务，永久失败保留 request ID 供重放。
  useEffect(() => {
    if (!bound || !job || job.status === "draft" || job.status === "failed") return;
    let active = true;
    const contextAtStart = workbenchEpoch.current;
    const timer = setTimeout(() => {
      api
        .getJob(job.jobId)
        .then((next) => {
          if (!active) return;
          setJobPollFailed(false);
          setJob({ jobId: job.jobId, status: next.status });
          if (next.status === "draft") {
            generateRequestId.current = null;
            sessionStorage.removeItem(jobStorageKey);
            sessionStorage.removeItem(requestStorageKey);
            // 归属校验：回读版本列表，确认新版本属于当前报告
            setNonce((n) => n + 1);
            api
              .listVersions(reportId)
              .then((items) => {
                if (contextAtStart !== workbenchEpoch.current) return;
                if (items.some((v) => v.version_id === next.version_id)) {
                  message.success("生成完成，已进入草稿");
                  setSelectedId(next.version_id);
                } else {
                  message.error("版本归属校验失败，已为你刷新");
                }
              })
              .catch(() => {
                if (contextAtStart !== workbenchEpoch.current) return;
                message.error("生成结果确认失败，请刷新重试");
              });
          } else if (next.status === "failed") {
            sessionStorage.removeItem(jobStorageKey);
            setJob(null);
            if (generationFailureCanResume(next.error_reason)) {
              // 后端只允许使用原 request ID 重放可恢复的终态任务。
              message.warning("生成服务中断，可恢复原生成任务");
            } else {
              generateRequestId.current = null;
              sessionStorage.removeItem(requestStorageKey);
              message.error("生成失败，请检查材料后重新生成");
            }
            setNonce((n) => n + 1);
          }
        })
        .catch((error) => {
          if (!active) return;
          const kind = errorKind(error);
          const retryable =
            error instanceof ApiError
              ? error.retryable || error.code === "REQUEST_ABORTED"
              : kind === "network" || kind === "unavailable";
          if (retryable) {
            // 短暂中断：保留任务上下文，允许继续查询原任务。
            setJobPollFailed(true);
            return;
          }
          // 旧任务已永久失效：只清 job，保留 request ID 以重放原请求。
          sessionStorage.removeItem(jobStorageKey);
          setJob(null);
          setJobPollFailed(false);
          message.error("生成任务已失效，请恢复原请求");
        });
    }, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, job, jobStorageKey, requestStorageKey, reportId, bound]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const current = versions?.find((v) => v.version_id === selectedId) ?? null;
  const status = current?.status ?? (versions && versions.length === 0 ? "empty" : null);
  const checklistComplete =
    status === "approved" || status === "published" || status === "superseded" || status === "withdrawn";
  const approveReady = REVIEW_CHECKLIST.every((item) => checked[item.key]);

  const runTransition = async (
    action: TransitionAction,
    evidence?: TransitionEvidence,
  ): Promise<boolean> => {
    if (!selectedId) return false;
    setBusy(true);
    try {
      await api.transition(selectedId, action, evidence);
      message.success(
        { submit: "已提交审核", return: "已退回", approve: "已批准", publish: "已发布", withdraw: "已撤回" }[action],
      );
      refresh();
      return true;
    } catch (e) {
      if (errorKind(e) === "conflict") {
        message.warning("状态已变化，已为你刷新到最新状态");
        refresh();
      } else {
        message.error("操作未完成，请重试");
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submitReturn = async () => {
    const comment = returnComment.trim();
    if (!comment) {
      message.warning("请填写退回原因");
      return;
    }
    if (await runTransition("return", { comment })) {
      setReturnOpen(false);
      setReturnComment("");
    }
  };

  const confirmWithdraw = () => {
    Modal.confirm({
      title: "撤回此版本？",
      content: "撤回后客户将立即看不到此版本，该操作需要重新生成并发布才能恢复。",
      okText: "确认",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () => runTransition("withdraw"),
    });
  };

  const confirmPublish = () => {
    Modal.confirm({
      title: "发布此版本？",
      content: "发布后客户立即可见；同一份报告的旧发布版本将自动标记为已被替代。",
      okText: "发布",
      cancelText: "取消",
      onOk: () => runTransition("publish"),
    });
  };

  const generate = async () => {
    if (!clientId) return;
    setBusy(true);
    if (!generateRequestId.current) {
      generateRequestId.current = crypto.randomUUID();
      sessionStorage.setItem(requestStorageKey, generateRequestId.current);
    }
    try {
      const accepted = await api.generate(clientId, reportId, generateRequestId.current);
      if (accepted.status === "draft") {
        generateRequestId.current = null;
        sessionStorage.removeItem(requestStorageKey);
        sessionStorage.removeItem(jobStorageKey);
        message.success("生成完成，已进入草稿");
        refresh();
        setSelectedId(accepted.version_id);
      } else if (accepted.status === "failed") {
        sessionStorage.removeItem(jobStorageKey);
        setJob(null);
        try {
          const failed = await api.getJob(accepted.job_id);
          if (generationFailureCanResume(failed.error_reason)) {
            message.warning("生成服务中断，可恢复原生成任务");
          } else {
            generateRequestId.current = null;
            sessionStorage.removeItem(requestStorageKey);
            message.error("生成失败，请检查材料后重新生成");
          }
        } catch {
          // 无法确认失败类型时保留原 request ID，避免丢掉可恢复任务。
          message.warning("暂时无法确认生成状态，可恢复原任务");
        }
        refresh();
      } else {
        setJob({ jobId: accepted.job_id, status: accepted.status });
        sessionStorage.setItem(jobStorageKey, accepted.job_id);
      }
    } catch (e) {
      if (errorKind(e) === "notFound") {
        generateRequestId.current = null;
        sessionStorage.removeItem(requestStorageKey);
        message.error("可用材料不足或报告不存在，无法生成");
      } else if (errorKind(e) === "conflict") {
        generateRequestId.current = null;
        sessionStorage.removeItem(requestStorageKey);
        message.warning("请求冲突，已为你刷新");
        refresh();
      } else if (errorKind(e) !== "network" && errorKind(e) !== "unavailable") {
        generateRequestId.current = null;
        sessionStorage.removeItem(requestStorageKey);
        message.error("生成请求失败，请重试");
      } else {
        message.error("生成请求失败，请重试");
      }
    } finally {
      setBusy(false);
    }
  };

  const downloadHtml = async () => {
    if (!selectedId) return;
    setDownloading(true);
    try {
      const artifact = await api.getVersionHtmlArtifact(selectedId);
      saveHtmlReportArtifact(artifact);
      message.success("HTML 报告已开始下载");
    } catch {
      message.error("HTML 报告下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

  const downloadPdf = async () => {
    if (!selectedId) return;
    setDownloading(true);
    try {
      const artifact = await api.getVersionPdfArtifact(selectedId);
      saveHtmlReportArtifact(artifact);
      message.success("PDF 报告已开始下载");
    } catch {
      message.error("PDF 报告下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

  if (listError) {
    return <ErrorState error={listError} onRetry={refresh} />;
  }
  if (!versions) {
    return <Spin style={{ display: "block", margin: "96px auto" }} />;
  }

  const generating =
    job !== null && job.status !== "draft" && job.status !== "failed";
  const generationRecoveryPending = job === null && generateRequestId.current !== null;

  return (
    <main className="console-page workbench-page">
      <div className="workbench-back">
        <Link to={clientId ? `/console/clients/${clientId}/reports` : "/console/clients"}>
          ← 返回报告列表
        </Link>
      </div>
      <div className="workbench-grid">
        <div className="workbench-doc">
          <header className="workbench-doc__header">
            <Typography.Title level={2}>
              企业安环资料分析报告
            </Typography.Title>
            <div className="workbench-doc__meta">
              {current ? <span>第 {current.version_number} 版</span> : null}
              <StatusDot
                tone={STATUS_TONE[status ?? "empty"]}
                label={REPORT_STATUS_LABEL[status ?? "empty"]}
              />
            </div>
          </header>
          {detailError ? (
            <ErrorState error={detailError} onRetry={refresh} />
          ) : !current ? (
            <Typography.Paragraph type="secondary">
              空报告：尚无版本。请在操作区生成首个版本。
            </Typography.Paragraph>
          ) : detail && detail.sections.length > 0 ? (
            <ReportDocument sections={detail.sections} citations={detail.citations} serif />
          ) : (
            <Typography.Paragraph type="secondary">
              {status === "generating" || status === "queued"
                ? "该版本正在生成，尚无内容。"
                : "该版本暂无内容。"}
            </Typography.Paragraph>
          )}
        </div>
        <div className="workbench-panel">
          <section className="workbench-panel__section">
              <Typography.Text strong>生成进度</Typography.Text>
              <div style={{ marginTop: 8 }}>
                {jobPollFailed && job ? (
                  <div>
                    <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                      状态查询中断，生成可能仍在进行。
                    </Typography.Paragraph>
                    <Button
                      size="small"
                      onClick={() => {
                        setJobPollFailed(false);
                        setJob({ jobId: job.jobId, status: "generating" });
                      }}
                    >
                      重新查询
                    </Button>
                  </div>
                ) : generating ? (
                  <StatusDot tone="processing" label="正在生成，通常需要数十秒…" />
                ) : (
                  <StatusDot
                    tone={STATUS_TONE[status ?? "empty"]}
                    label={REPORT_STATUS_LABEL[status ?? "empty"]}
                  />
                )}
              </div>
            </section>

            <section className="workbench-panel__section">
              <Typography.Text strong>审核清单</Typography.Text>
              <div className="workbench-checklist">
                {REVIEW_CHECKLIST.map((item) => (
                  <div
                    key={item.key}
                    data-review-check={item.key}
                    data-checked={checked[item.key] || checklistComplete ? "1" : "0"}
                    style={{ cursor: status === "review_pending" ? "pointer" : "not-allowed" }}
                    onClick={() => {
                      if (status !== "review_pending") return;
                      setChecked((prev) => ({
                        ...prev,
                        [item.key]: !prev[item.key],
                      }));
                    }}
                  >
                    <Checkbox
                      checked={checked[item.key] || checklistComplete}
                      disabled={status !== "review_pending"}
                      style={{ pointerEvents: "none" }}
                    >
                      {item.label}
                    </Checkbox>
                  </div>
                ))}
              </div>
            </section>

            <section className="workbench-panel__section">
              <Typography.Text strong>审核记录</Typography.Text>
              <div data-review-events-readonly="1" style={{ marginTop: 8 }}>
                {!detail || detail.review_events.length === 0 ? (
                  <Typography.Text type="secondary">尚无审核记录</Typography.Text>
                ) : (
                  [...detail.review_events].reverse().map((event, index) => {
                    const checklistItems = REVIEW_CHECKLIST.filter(
                      (item) => event.checklist[item.key] !== undefined,
                    );
                    return (
                      <div
                        key={event.event_id}
                        data-review-event={event.action}
                        style={{
                          padding: "8px 0",
                          borderBottom:
                            index < detail.review_events.length - 1
                              ? "1px solid var(--eco-border)"
                              : undefined,
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 8,
                          }}
                        >
                          <Typography.Text>{REVIEW_ACTION_LABEL[event.action]}</Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {formatDateTime(event.created_at)}
                          </Typography.Text>
                        </div>
                        {checklistItems.length > 0 && (
                          <Typography.Paragraph
                            type="secondary"
                            style={{ margin: "4px 0 0", fontSize: 12 }}
                          >
                            {checklistItems
                              .map(
                                (item) =>
                                  `${item.label}：${event.checklist[item.key] ? "已确认" : "未确认"}`,
                              )
                              .join("；")}
                          </Typography.Paragraph>
                        )}
                        {event.comment && (
                          <Typography.Paragraph style={{ margin: "4px 0 0", fontSize: 12 }}>
                            {event.action === "return" ? "退回原因" : "审核备注"}：
                            {event.comment}
                          </Typography.Paragraph>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </section>

            <section className="workbench-panel__section">
              <Typography.Text strong>版本历史</Typography.Text>
              <div className="workbench-version-list">
                {versions.length === 0 && (
                  <Typography.Text type="secondary">尚无版本</Typography.Text>
                )}
                {[...versions].reverse().map((v) => (
                  <div key={v.version_id} style={{ padding: "6px 0" }}>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0 }}
                      onClick={() => setSelectedId(v.version_id)}
                    >
                      第 {v.version_number} 版
                    </Button>
                    {v.version_id === selectedId && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {" "}
                        当前
                      </Typography.Text>
                    )}
                    <div>
                      <StatusDot
                        tone={STATUS_TONE[v.status]}
                        label={REPORT_STATUS_LABEL[v.status]}
                      />
                      <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        {formatDateTime(v.created_at)}
                      </Typography.Text>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section
              className="workbench-panel__section workbench-panel__section--actions"
              data-report-status={status ?? ""}
              data-approve-ready={status === "review_pending" && approveReady ? "1" : "0"}
            >
              <Typography.Text strong>操作</Typography.Text>
              <div className="workbench-action-stack">
                {selectedId && detail && detail.sections.length > 0 && (
                  <>
                    <Button
                      data-report-action="download-pdf"
                      loading={downloading}
                      disabled={busy}
                      onClick={() => void downloadPdf()}
                    >
                      下载 PDF 报告
                    </Button>
                    <Button
                      data-report-action="download-html"
                      loading={downloading}
                      disabled={busy}
                      onClick={() => void downloadHtml()}
                    >
                      下载 HTML 报告
                    </Button>
                  </>
                )}
                {capabilities.has("generate") &&
                  (status === "empty" || status === "changes_requested" || status === "failed" ||
                  status === "superseded" || status === "withdrawn" || generationRecoveryPending) && (
                  <Button
                    type="primary"
                    loading={busy}
                    disabled={!clientId || generating}
                    onClick={() => void generate()}
                  >
                    {generationRecoveryPending
                      ? "恢复生成任务"
                      : status === "empty"
                        ? "生成首个版本"
                        : "生成新版本"}
                  </Button>
                )}
                {status === "draft" && capabilities.has("review") && (
                  <Button type="primary" loading={busy} onClick={() => void runTransition("submit")}>
                    提交审核
                  </Button>
                )}
                {status === "review_pending" && capabilities.has("review") && (
                  <>
                    <Button
                      type="primary"
                      loading={busy}
                      disabled={!approveReady}
                      onClick={() => void runTransition("approve", { checklist: checked })}
                    >
                      批准
                    </Button>
                    <Button
                      danger
                      loading={busy}
                      onClick={() => setReturnOpen(true)}
                    >
                      退回
                    </Button>
                  </>
                )}
                {status === "approved" && capabilities.has("publish") && (
                  <Button type="primary" loading={busy} onClick={confirmPublish}>
                    发布
                  </Button>
                )}
                {status === "published" && capabilities.has("withdraw") && (
                  <Button
                    danger
                    loading={busy}
                    onClick={confirmWithdraw}
                  >
                    撤回
                  </Button>
                )}
              </div>
            </section>
        </div>
      </div>
      <Modal
        open={returnOpen}
        title="退回此版本"
        okText="确认退回"
        cancelText="取消"
        confirmLoading={busy}
        closable={!busy}
        maskClosable={!busy}
        cancelButtonProps={{ disabled: busy }}
        okButtonProps={{ danger: true, disabled: returnComment.trim().length === 0 }}
        onCancel={() => {
          if (!busy) {
            setReturnOpen(false);
            setReturnComment("");
          }
        }}
        onOk={() => void submitReturn()}
      >
        <Typography.Paragraph type="secondary">
          退回后该版本不可直接修改，需生成新版本继续编辑。退回原因将作为只读审核记录保留。
        </Typography.Paragraph>
        <Input.TextArea
          aria-label="退回原因"
          autoFocus
          value={returnComment}
          rows={4}
          maxLength={2_000}
          showCount
          placeholder="请说明需要修改的问题"
          onChange={(event) => setReturnComment(event.target.value)}
        />
      </Modal>
    </main>
  );
}
