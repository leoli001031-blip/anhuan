// 运营台 · 报告工作台（/console/clients/:clientId/reports/:reportId）
// ≥1280px 左文档右审核；<1280px 正文在上、审核区在下，全部操作保持可见。
// 危险操作（退回、撤回）二次确认；409 映射为「状态已变化」并自动刷新。
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Checkbox, Modal, Spin, Typography, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useApi, useSessionAccess } from "../../adapters";
import { ApiError, errorKind } from "../../adapters/errors";
import type {
  ReportStatus,
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

const CHECKLIST = ["引用证据可溯源", "风险与缺口表述完整", "使用边界已包含"];

export default function ReportWorkbenchPage() {
  const { reportId = "", clientId = "" } = useParams();
  const api = useApi();
  const { session } = useSessionAccess();
  const capabilities = new Set(session?.capabilities ?? []);
  const generateRequestId = useRef<string | null>(null);
  const detailEpoch = useRef(0);

  const [versions, setVersions] = useState<VersionHistoryItemV1[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VersionDetailV1 | null>(null);
  const [detailError, setDetailError] = useState<unknown>(null);
  const [checked, setChecked] = useState<boolean[]>(CHECKLIST.map(() => false));
  const [job, setJob] = useState<{ jobId: string; status: string } | null>(null);
  const [jobPollFailed, setJobPollFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0);
  // 绑定证明：reportId 必须先出现在该客户的报告列表里，否则禁止读版本与执行动作。
  const [bound, setBound] = useState(false);

  // 生成任务上下文按报告持久化（sessionStorage），查询中断可恢复重查。
  const jobStorageKey = `ar-job:${reportId}`;

  // 路由客户/报告变化：先清旧状态（版本/选中/详情/任务/请求ID），不残留上一上下文。
  useEffect(() => {
    setVersions(null);
    setListError(null);
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    setChecked(CHECKLIST.map(() => false));
    setJobPollFailed(false);
    setBound(false);
    generateRequestId.current = null;
    detailEpoch.current += 1;
    const saved = sessionStorage.getItem(jobStorageKey);
    setJob(saved ? { jobId: saved, status: "generating" } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId, clientId]);

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

  useEffect(() => {
    setChecked(CHECKLIST.map(() => false));
  }, [selectedId]);

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

  // 生成进度轮询：终态即停并校验版本归属；查询失败保留上下文、允许重查。绑定后才轮询。
  useEffect(() => {
    if (!bound || !job || job.status === "draft" || job.status === "failed") return;
    const timer = setTimeout(() => {
      api
        .getJob(job.jobId)
        .then((next) => {
          setJobPollFailed(false);
          setJob({ jobId: job.jobId, status: next.status });
          if (next.status === "draft") {
            generateRequestId.current = null;
            sessionStorage.removeItem(jobStorageKey);
            // 归属校验：回读版本列表，确认新版本属于当前报告
            setNonce((n) => n + 1);
            api
              .listVersions(reportId)
              .then((items) => {
                if (items.some((v) => v.version_id === next.version_id)) {
                  message.success("生成完成，已进入草稿");
                  setSelectedId(next.version_id);
                } else {
                  message.error("版本归属校验失败，已为你刷新");
                }
              })
              .catch(() => message.error("生成结果确认失败，请刷新重试"));
          } else if (next.status === "failed") {
            generateRequestId.current = null;
            sessionStorage.removeItem(jobStorageKey);
            message.error("生成失败，请检查材料后重新生成");
            setNonce((n) => n + 1);
          }
        })
        .catch(() => {
          // 状态查询中断：保留任务上下文，等待人工重查
          setJobPollFailed(true);
        });
    }, 2000);
    return () => clearTimeout(timer);
  }, [api, job, jobStorageKey, reportId, bound]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const current = versions?.find((v) => v.version_id === selectedId) ?? null;
  const status = current?.status ?? (versions && versions.length === 0 ? "empty" : null);
  const checklistComplete =
    status === "approved" || status === "published" || status === "superseded" || status === "withdrawn";

  const runTransition = async (
    action: "submit" | "return" | "approve" | "publish" | "withdraw",
  ) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await api.transition(selectedId, action);
      message.success(
        { submit: "已提交审核", return: "已退回", approve: "已批准", publish: "已发布", withdraw: "已撤回" }[action],
      );
      refresh();
    } catch (e) {
      if (errorKind(e) === "conflict") {
        message.warning("状态已变化，已为你刷新到最新状态");
        refresh();
      } else {
        message.error("操作未完成，请重试");
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmDanger = (title: string, content: string, action: "return" | "withdraw") => {
    Modal.confirm({
      title,
      content,
      okText: "确认",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () => runTransition(action),
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
    if (!generateRequestId.current) generateRequestId.current = crypto.randomUUID();
    try {
      const accepted = await api.generate(clientId, reportId, generateRequestId.current);
      if (accepted.status === "draft" || accepted.status === "failed") {
        generateRequestId.current = null;
      }
      if (accepted.status === "draft") {
        message.success("生成完成，已进入草稿");
        refresh();
        setSelectedId(accepted.version_id);
      } else if (accepted.status === "failed") {
        message.error("生成失败，请检查材料后重新生成");
        refresh();
      } else {
        setJob({ jobId: accepted.job_id, status: accepted.status });
        sessionStorage.setItem(jobStorageKey, accepted.job_id);
      }
    } catch (e) {
      if (errorKind(e) === "notFound") {
        generateRequestId.current = null;
        message.error("可用材料不足或报告不存在，无法生成");
      } else if (errorKind(e) === "conflict") {
        generateRequestId.current = null;
        message.warning("请求冲突，已为你刷新");
        refresh();
      } else if (errorKind(e) !== "network" && errorKind(e) !== "unavailable") {
        generateRequestId.current = null;
        message.error("生成请求失败，请重试");
      } else {
        message.error("生成请求失败，请重试");
      }
    } finally {
      setBusy(false);
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
                {CHECKLIST.map((item, i) => (
                  <div
                    key={item}
                    data-review-check={item}
                    data-checked={checked[i] || checklistComplete ? "1" : "0"}
                    style={{ cursor: status === "review_pending" ? "pointer" : "not-allowed" }}
                    onClick={() => {
                      if (status !== "review_pending") return;
                      setChecked((prev) => prev.map((v, j) => (j === i ? !v : v)));
                    }}
                  >
                    <Checkbox
                      checked={checked[i] || checklistComplete}
                      disabled={status !== "review_pending"}
                      style={{ pointerEvents: "none" }}
                    >
                      {item}
                    </Checkbox>
                  </div>
                ))}
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
              data-approve-ready={status === "review_pending" && checked.every(Boolean) ? "1" : "0"}
            >
              <Typography.Text strong>操作</Typography.Text>
              <div className="workbench-action-stack">
                {capabilities.has("generate") &&
                  (status === "empty" || status === "changes_requested" || status === "failed" ||
                  status === "superseded" || status === "withdrawn") && (
                  <Button
                    type="primary"
                    loading={busy}
                    disabled={!clientId || generating}
                    onClick={() => void generate()}
                  >
                    {status === "empty" ? "生成首个版本" : "生成新版本"}
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
                      disabled={!checked.every(Boolean)}
                      onClick={() => void runTransition("approve")}
                    >
                      批准
                    </Button>
                    <Button
                      danger
                      loading={busy}
                      onClick={() =>
                        confirmDanger(
                          "退回此版本？",
                          "退回后该版本不可直接修改，需生成新版本继续编辑。",
                          "return",
                        )
                      }
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
                    onClick={() =>
                      confirmDanger(
                        "撤回此版本？",
                        "撤回后客户将立即看不到此版本，该操作需要重新生成并发布才能恢复。",
                        "withdraw",
                      )
                    }
                  >
                    撤回
                  </Button>
                )}
              </div>
            </section>
        </div>
      </div>
    </main>
  );
}
