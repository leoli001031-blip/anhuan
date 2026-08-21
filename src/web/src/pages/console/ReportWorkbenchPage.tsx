// 运营台 · 报告工作台（/console/clients/:clientId/reports/:reportId）
// 左 60% 文档预览（与客户所见同版式），右 40%：生成进度 / 审核清单 / 版本历史 / 操作。
// 危险操作（退回、撤回）二次确认；409 映射为「状态已变化」并自动刷新。
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Checkbox, Col, Modal, Row, Spin, Typography, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import { errorKind } from "../../adapters/errors";
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
  const generateRequestId = useRef<string | null>(null);

  const [versions, setVersions] = useState<VersionHistoryItemV1[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VersionDetailV1 | null>(null);
  const [detailError, setDetailError] = useState<unknown>(null);
  const [checked, setChecked] = useState<boolean[]>(CHECKLIST.map(() => false));
  const [job, setJob] = useState<{ jobId: string; status: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0);

  // 版本历史
  useEffect(() => {
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
  }, [api, reportId, nonce]);

  // 选中版本的草稿/审核详情
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetailError(null);
    setChecked(CHECKLIST.map(() => false));
    api
      .getVersion(selectedId)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => {
        if (active) setDetailError(e);
      });
    return () => {
      active = false;
    };
  }, [api, selectedId, nonce]);

  // 生成进度轮询：终态即停，失败只给业务文案
  useEffect(() => {
    if (!job || job.status === "draft" || job.status === "failed") return;
    const timer = setTimeout(() => {
      api
        .getJob(job.jobId)
        .then((next) => {
          setJob({ jobId: job.jobId, status: next.status });
          if (next.status === "draft") {
            generateRequestId.current = null;
            message.success("生成完成，已进入草稿");
            setNonce((n) => n + 1);
            setSelectedId(next.version_id);
          } else if (next.status === "failed") {
            generateRequestId.current = null;
            message.error("生成失败，请检查材料后重新生成");
            setNonce((n) => n + 1);
          }
        })
        .catch(() => setJob(null));
    }, 2000);
    return () => clearTimeout(timer);
  }, [api, job]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const current = versions?.find((v) => v.version_id === selectedId) ?? null;
  const status = current?.status ?? (versions && versions.length === 0 ? "empty" : null);

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
    <div style={{ maxWidth: 1200 }}>
      <div style={{ marginBottom: 16 }}>
        <Link to={clientId ? `/console/clients/${clientId}/reports` : "/console/clients"}>
          ← 返回报告列表
        </Link>
      </div>
      <Row gutter={32}>
        <Col flex="auto" style={{ minWidth: 0 }}>
          <Typography.Title level={4} style={{ marginTop: 0 }}>
            企业安环资料分析报告
            {current ? ` · 第 ${current.version_number} 版` : ""}
          </Typography.Title>
          {detailError ? (
            <ErrorState error={detailError} onRetry={refresh} />
          ) : !current ? (
            <Typography.Paragraph type="secondary">
              空报告：尚无版本。请在右侧生成首个版本。
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
        </Col>
        <Col flex="360px">
          <div
            style={{
              borderLeft: "1px solid var(--eco-border)",
              paddingLeft: 24,
              display: "flex",
              flexDirection: "column",
              gap: 24,
            }}
          >
            <section>
              <Typography.Text strong>生成进度</Typography.Text>
              <div style={{ marginTop: 8 }}>
                {generating ? (
                  <StatusDot tone="processing" label="正在生成，通常需要数十秒…" />
                ) : (
                  <StatusDot
                    tone={STATUS_TONE[status ?? "empty"]}
                    label={REPORT_STATUS_LABEL[status ?? "empty"]}
                  />
                )}
              </div>
            </section>

            <section>
              <Typography.Text strong>审核清单</Typography.Text>
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
                {CHECKLIST.map((item, i) => (
                  <Checkbox
                    key={item}
                    checked={checked[i]}
                    disabled={status !== "review_pending"}
                    onChange={(e) =>
                      setChecked((prev) => prev.map((v, j) => (j === i ? e.target.checked : v)))
                    }
                  >
                    {item}
                  </Checkbox>
                ))}
              </div>
            </section>

            <section>
              <Typography.Text strong>版本历史</Typography.Text>
              <div style={{ marginTop: 8 }}>
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

            <section>
              <Typography.Text strong>操作</Typography.Text>
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                {(status === "empty" || status === "changes_requested" || status === "failed" ||
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
                {status === "draft" && (
                  <Button type="primary" loading={busy} onClick={() => void runTransition("submit")}>
                    提交审核
                  </Button>
                )}
                {status === "review_pending" && (
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
                {status === "approved" && (
                  <Button type="primary" loading={busy} onClick={confirmPublish}>
                    发布
                  </Button>
                )}
                {status === "published" && (
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
        </Col>
      </Row>
    </div>
  );
}
