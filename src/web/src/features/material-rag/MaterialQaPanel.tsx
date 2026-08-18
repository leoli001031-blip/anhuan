import { Alert, Button, Empty, Select, Space, Spin, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../api";
import { useAuth } from "../../auth/OidcProvider";
import {
  askFixedQuery,
  deleteScope,
  localUatRuntimeEnabled,
  openCitation,
  rebuildScope,
} from "./api";
import { PHASE_COPY, reasonCopy } from "./journeyMachine";
import type { ClosedQueryId, MaterialCitation, MaterialQaUiState } from "./types";
import { CLOSED_QUERY_IDS, QUERY_LABELS } from "./types";

function newRequestId(): string {
  return crypto.randomUUID();
}

const INITIAL: MaterialQaUiState = {
  phase: localUatRuntimeEnabled() ? "empty" : "disabled",
  answer: null,
  citations: [],
  code: localUatRuntimeEnabled() ? null : "LIVE_RETRIEVAL_BLOCKED_BY_KEY_ROTATION",
  scopeLabel: "未选择范围",
};

interface Props {
  clientAccountId?: string | null;
  initialQueryId?: ClosedQueryId;
}

export default function MaterialQaPanel({ clientAccountId, initialQueryId }: Props) {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const enabled = localUatRuntimeEnabled();
  const [queryId, setQueryId] = useState<ClosedQueryId>(
    initialQueryId ?? (clientAccountId ? "client.current" : "provider.shared"),
  );
  const [requestId, setRequestId] = useState(newRequestId);
  const [reuseRequestId, setReuseRequestId] = useState(false);
  const [ui, setUi] = useState<MaterialQaUiState>(INITIAL);
  const [busy, setBusy] = useState(false);
  const [citationError, setCitationError] = useState<string | null>(null);

  const queryOptions = useMemo(
    () => CLOSED_QUERY_IDS.map((value) => ({ value, label: QUERY_LABELS[value] })),
    [],
  );

  const submitDisabled = !enabled || busy || ui.phase === "disabled";

  async function runAsk() {
    if (!enabled) return;
    setCitationError(null);
    setBusy(true);
    setUi((current) => ({ ...current, phase: "loading" }));
    const nextRequestId = reuseRequestId ? requestId : newRequestId();
    setRequestId(nextRequestId);
    const next = await askFixedQuery(getAccessToken(), {
      queryId,
      requestId: nextRequestId,
      clientAccountId,
      previous: { ...ui, phase: "loading" },
    });
    setUi(next);
    setBusy(false);
  }

  async function runDelete() {
    if (!enabled) return;
    setBusy(true);
    try {
      const deleted = await deleteScope(getAccessToken(), clientAccountId);
      setUi({
        phase: deleted.residual_count === 0 ? "empty" : "retry",
        answer: null,
        citations: [],
        code: null,
        scopeLabel: ui.scopeLabel,
      });
    } catch (reason) {
      const code = reason instanceof ApiError ? reason.code : "RETRY";
      setUi({
        phase: "retry",
        answer: null,
        citations: [],
        code,
        scopeLabel: ui.scopeLabel,
      });
    } finally {
      setBusy(false);
    }
  }

  async function runRebuild() {
    if (!enabled) return;
    setBusy(true);
    try {
      await rebuildScope(getAccessToken(), clientAccountId);
      setUi({
        phase: "recovery",
        answer: null,
        citations: [],
        code: null,
        scopeLabel: ui.scopeLabel,
      });
    } catch (reason) {
      const code = reason instanceof ApiError ? reason.code : "RETRY";
      setUi({
        phase: "retry",
        answer: null,
        citations: [],
        code,
        scopeLabel: ui.scopeLabel,
      });
    } finally {
      setBusy(false);
    }
  }

  async function jump(citation: MaterialCitation) {
    setCitationError(null);
    try {
      const opened = await openCitation(getAccessToken(), citation);
      navigate(
        `/controlled-documents/${opened.document_record_id}?version=${opened.document_version_id}&page=${opened.page_number}`,
      );
    } catch (reason) {
      setUi((current) => ({
        ...current,
        phase: "denied",
        answer: null,
        citations: [],
        code:
          reason instanceof ApiError ? reason.code : "MATERIAL_CITATION_NOT_FOUND",
      }));
      setCitationError("引用不存在或当前范围无权访问。");
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        data-testid="material-rag-live-blocked"
        message="真实 Ark 检索未通过"
        description={reasonCopy("LIVE_RETRIEVAL_BLOCKED_BY_KEY_ROTATION")}
      />
      <Alert
        type={enabled ? "info" : "warning"}
        showIcon
        data-testid={`material-rag-phase-${ui.phase}`}
        message={PHASE_COPY[ui.phase]}
        description={
          clientAccountId
            ? "当前入口绑定客户详情中的客户域；不会回退其他客户或历史企业集。"
            : "未携带客户时只检索服务商共享域。页面没有自由文本输入。"
        }
      />
      <Space wrap>
        <Select
          data-testid="material-rag-query"
          style={{ minWidth: 280 }}
          value={queryId}
          disabled={submitDisabled && ui.phase === "disabled"}
          options={queryOptions}
          onChange={(value: ClosedQueryId) => setQueryId(value)}
        />
        <Button data-testid="material-rag-ask" type="primary" loading={busy} disabled={submitDisabled} onClick={() => void runAsk()}>
          按固定场景检索
        </Button>
        <Button data-testid="material-rag-retry" disabled={submitDisabled} onClick={() => void runAsk()}>
          重试
        </Button>
        <Button disabled={submitDisabled} onClick={() => void runRebuild()}>
          重建当前范围
        </Button>
        <Button danger disabled={submitDisabled} onClick={() => void runDelete()}>
          删除当前范围
        </Button>
        <Button
          disabled={!enabled || busy}
          onClick={() => setReuseRequestId((current) => !current)}
        >
          {reuseRequestId ? "将复用请求标识" : "新请求标识"}
        </Button>
      </Space>
      {ui.phase === "loading" ? <Spin data-testid="material-rag-loading" tip="正在按当前范围检索" /> : null}
      <Typography.Text type="secondary" data-testid="material-rag-scope">
        当前范围：{ui.scopeLabel}
        {clientAccountId ? " · 已绑定客户详情入口" : " · 服务商入口"}
      </Typography.Text>
      {ui.code ? (
        <Tag data-testid="material-rag-code" color={ui.phase === "ready" ? "green" : "orange"}>
          {reasonCopy(ui.code)}
        </Tag>
      ) : null}
      {citationError ? <Alert type="error" showIcon message={citationError} /> : null}
      {ui.answer ? (
        <Typography.Paragraph data-testid="material-rag-answer">{ui.answer}</Typography.Paragraph>
      ) : (
        <Empty data-testid="material-rag-empty-answer" description="没有可展示的答案" />
      )}
      <Table<MaterialCitation>
        data-testid="material-rag-citations"
        rowKey="canonical_unit_id"
        dataSource={ui.citations}
        pagination={false}
        locale={{ emptyText: "没有可跳转的引用" }}
        columns={[
          {
            title: "文档",
            dataIndex: "document_name",
          },
          {
            title: "范围",
            dataIndex: "scope_kind",
            render: (value: MaterialCitation["scope_kind"]) =>
              value === "client" ? "当前客户域" : "服务商共享域",
          },
          {
            title: "页",
            dataIndex: "page_number",
            width: 72,
          },
          {
            title: "操作",
            key: "jump",
            width: 100,
            render: (_, citation) => (
              <Button type="link" onClick={() => void jump(citation)}>
                打开引用
              </Button>
            ),
          },
        ]}
      />
    </Space>
  );
}
