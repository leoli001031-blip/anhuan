// 材料入库面板（共享域 / 客户域通用，两域从不聚合）。
// 正式 HTTP：完整复用 P3 受控入库能力——状态只来自后端可证明的上传/扫描/隔离/解析字段；
// 动作只消费 capabilities 与 allowed_actions；按钮在无权限或扫描不可用时隐藏。
// 入库完成不代表可生成报告，生成资格由后端在生成时校验（前端不推导）。
// 演示环境（mock）：保留简表走查，不伪造生命周期。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Spin,
  Table,
  Typography,
  Upload,
  message,
} from "antd";
import { isMockData, useApi } from "../adapters";
import { ApiError } from "../adapters/errors";
import type { MaterialItem, MaterialStatus } from "../adapters/types";
import { MATERIAL_STATUS_LABEL } from "../adapters/types";
import { useAuth } from "../auth/OidcProvider";
import { useNarrow } from "../pages/console/useNarrow";
import {
  createIngestionDocument,
  getIngestionCapabilities,
  getIngestionDocument,
  getMaterialIntakeAnalysis,
  getPageTextUnit,
  getPreviewImageUnitBlob,
  getPreviewManifest,
  listIngestionDocuments,
  processIngestionVersion,
  releaseIngestionVersion,
  rejectIngestionVersion,
  retryIngestionVersion,
  setMaterialIntakeClassification,
  userFacingIngestionError,
} from "../features/p3/ingestionApi";
import { reasonCopy } from "../features/p3/reasonCopy";
import type {
  DocumentCollection,
  DocumentSummary,
  IngestionCapabilities,
  MaterialIntakeAnalysis,
  MaterialKind,
  VersionSummary,
} from "../features/p3/types";
import ErrorState from "./ErrorState";
import StatusDot, { type StatusTone } from "./StatusDot";
import { formatDateTime } from "./ReportDocument";

const MATERIAL_KIND_LABEL: Record<MaterialKind, string> = {
  policy: "制度文件",
  report: "报告",
  unknown: "未分类",
};

// 内部状态 → 业务阶段。只映射后端可证明字段；入库完成≠具备生成资格。
type BusinessStage =
  | "等待处理"
  | "安全检查"
  | "文字识别与解析"
  | "待确认"
  | "入库处理完成"
  | "处理失败"
  | "已停用";

function businessStageOf(version: VersionSummary | null): BusinessStage {
  if (!version) return "等待处理";
  if (version.workflow_status === "failed") return "处理失败";
  if (version.scan_status === "infected" || version.scan_status === "error") {
    return "处理失败";
  }
  if (version.quarantine_status === "blocked") return "已停用";
  if (
    version.scan_status === "queued" ||
    version.scan_status === "scanning" ||
    version.scan_status === "unavailable"
  ) {
    return "安全检查";
  }
  if (
    version.workflow_status === "processing" ||
    version.preview_status === "queued" ||
    version.preview_status === "generating"
  ) {
    return "文字识别与解析";
  }
  if (
    version.workflow_status === "ready" &&
    version.quarantine_status === "released" &&
    version.scan_status === "clean"
  ) {
    return "入库处理完成";
  }
  // blocked 等其余状态：待确认，原因由 reason_code 说明
  return "待确认";
}

const STAGE_TONE: Record<BusinessStage, StatusTone> = {
  等待处理: "neutral",
  安全检查: "processing",
  文字识别与解析: "processing",
  待确认: "warning",
  入库处理完成: "success",
  处理失败: "danger",
  已停用: "neutral",
};

const ACTIVE_STAGES: ReadonlySet<BusinessStage> = new Set([
  "等待处理",
  "安全检查",
  "文字识别与解析",
]);

function stageOf(doc: DocumentSummary): BusinessStage {
  return businessStageOf(doc.latest_version);
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const anyError = error as { status?: number; code?: string; retryable?: boolean };
  return new ApiError(
    typeof anyError?.status === "number" ? anyError.status : 0,
    typeof anyError?.code === "string" ? anyError.code : "NETWORK_ERROR",
    Boolean(anyError?.retryable),
  );
}

// 响应复核：混入其他域/客户即 fail-closed
function assertScopePure(
  collection: DocumentCollection,
  scope: "shared" | "client",
  clientId?: string,
): void {
  const wantKind = scope === "shared" ? "service_provider" : "client";
  for (const item of collection.items) {
    if (item.knowledge_scope?.kind !== wantKind) {
      throw new ApiError(409, "SCOPE_MIXED", false);
    }
    if (
      scope === "client" &&
      clientId &&
      item.knowledge_scope.client_account_id !== clientId
    ) {
      throw new ApiError(409, "SCOPE_MIXED", false);
    }
  }
}

// —— 版本动作（只消费服务端 allowed_actions） ——

const VERSION_ACTIONS: Array<{
  action: "process" | "retry" | "release" | "reject";
  label: string;
  danger?: boolean;
}> = [
  { action: "process", label: "开始处理" },
  { action: "retry", label: "重试" },
  { action: "release", label: "确认可用" },
  { action: "reject", label: "停用", danger: true },
];

function VersionActions({
  version,
  onDone,
}: {
  version: VersionSummary;
  onDone: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (action: "process" | "retry" | "release" | "reject") => {
    setBusy(action);
    try {
      const fn = {
        process: processIngestionVersion,
        retry: retryIngestionVersion,
        release: releaseIngestionVersion,
        reject: rejectIngestionVersion,
      }[action];
      await fn(getAccessToken(), version.id);
      message.success(action === "reject" ? "已停用该版本" : "操作已提交");
      onDone();
    } catch (e) {
      message.error(userFacingIngestionError(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
      {VERSION_ACTIONS.filter((a) => version.allowed_actions.includes(a.action)).map((a) => (
        <Button
          key={a.action}
          size="small"
          danger={a.danger}
          loading={busy === a.action}
          onClick={() => void run(a.action)}
        >
          {a.label}
        </Button>
      ))}
    </span>
  );
}

// —— 安全预览（page_text 行 / image blob；其余诚实说明） ——

function VersionPreview({ versionId }: { versionId: string }) {
  const { getAccessToken } = useAuth();
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "text"; lines: string[] }
    | { kind: "image"; url: string }
    | { kind: "unavailable"; reason: string }
  >({ kind: "loading" });

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setState({ kind: "loading" });
    (async () => {
      try {
        const manifest = await getPreviewManifest(getAccessToken(), versionId);
        if (manifest.status !== "ready" || manifest.units.length === 0) {
          if (active) {
            setState({
              kind: "unavailable",
              reason:
                manifest.status === "generating" || manifest.status === "blocked"
                  ? "安全预览生成中"
                  : reasonCopy(manifest.reason_code ?? "PREVIEW_FAILED"),
            });
          }
          return;
        }
        const unit = manifest.units[0];
        if (unit.kind === "page_text") {
          const page = await getPageTextUnit(getAccessToken(), versionId, unit.id);
          if (active) setState({ kind: "text", lines: page.lines.slice(0, 12) });
        } else if (unit.kind === "image") {
          const blob = await getPreviewImageUnitBlob(getAccessToken(), versionId, unit.id);
          objectUrl = URL.createObjectURL(blob);
          if (active) setState({ kind: "image", url: objectUrl });
        } else {
          if (active) setState({ kind: "unavailable", reason: "表格文件暂不支持页内预览" });
        }
      } catch (e) {
        if (active) setState({ kind: "unavailable", reason: userFacingIngestionError(e) });
      }
    })();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [getAccessToken, versionId]);

  if (state.kind === "loading") return <Spin size="small" />;
  if (state.kind === "unavailable") {
    return <Typography.Text type="secondary">{state.reason}</Typography.Text>;
  }
  if (state.kind === "image") {
    return (
      <img
        src={state.url}
        alt="材料首页安全预览"
        style={{ maxWidth: "100%", border: "1px solid var(--eco-border)" }}
      />
    );
  }
  return (
    <div
      style={{
        border: "1px solid var(--eco-border)",
        padding: 12,
        fontSize: 13,
        lineHeight: 1.8,
        maxHeight: 240,
        overflow: "auto",
      }}
    >
      {state.lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

// —— 分类确认（仅在服务端允许时出现） ——

function ClassificationSection({
  versionId,
  onDone,
}: {
  versionId: string;
  onDone: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [analysis, setAnalysis] = useState<MaterialIntakeAnalysis | null>(null);
  const [absent, setAbsent] = useState(false);
  const [kind, setKind] = useState<MaterialKind>("unknown");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    getMaterialIntakeAnalysis(getAccessToken(), versionId)
      .then((a) => {
        if (active) {
          setAnalysis(a);
          setKind(a.resolved_kind);
        }
      })
      .catch(() => {
        if (active) setAbsent(true);
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, versionId]);

  if (absent || !analysis) return null;
  if (!analysis.allowed_actions.includes("set_material_kind")) {
    return (
      <Typography.Text type="secondary">
        分类：{MATERIAL_KIND_LABEL[analysis.resolved_kind]}
      </Typography.Text>
    );
  }
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Typography.Text type="secondary">分类</Typography.Text>
      <Select
        size="small"
        value={kind}
        style={{ width: 140 }}
        options={(Object.keys(MATERIAL_KIND_LABEL) as MaterialKind[]).map((k) => ({
          value: k,
          label: MATERIAL_KIND_LABEL[k],
        }))}
        onChange={(v) => setKind(v)}
      />
      <Button
        size="small"
        loading={saving}
        onClick={() => {
          setSaving(true);
          setMaterialIntakeClassification(getAccessToken(), analysis.id, kind)
            .then(() => {
              message.success("分类已保存");
              onDone();
            })
            .catch((e) => message.error(userFacingIngestionError(e)))
            .finally(() => setSaving(false));
        }}
      >
        保存分类
      </Button>
    </div>
  );
}

// —— 文档详情抽屉 ——

function DocumentDrawer({
  documentId,
  onClose,
}: {
  documentId: string;
  onClose: () => void;
}) {
  const { getAccessToken } = useAuth();
  const [detail, setDetail] = useState<DocumentSummary & { versions?: VersionSummary[] }>();
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setDetail(undefined);
    setError(null);
    getIngestionDocument(getAccessToken(), documentId)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => {
        if (active) setError(userFacingIngestionError(e));
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, documentId, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const latest = detail?.latest_version ?? null;

  return (
    <Drawer title={detail?.display_name ?? "材料详情"} width={560} open onClose={onClose}>
      {error ? (
        <Typography.Text type="danger">{error}</Typography.Text>
      ) : !detail ? (
        <Spin />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <section>
            <Typography.Text strong>当前状态</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <StatusDot tone={STAGE_TONE[stageOf(detail)]} label={stageOf(detail)} />
              {latest?.reason_code && (
                <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 13 }}>
                  {reasonCopy(latest.reason_code)}
                </Typography.Text>
              )}
            </div>
            {latest && (
              <div style={{ marginTop: 8 }}>
                <VersionActions version={latest} onDone={reload} />
              </div>
            )}
          </section>
          {latest && (
            <section>
              <Typography.Text strong>安全预览</Typography.Text>
              <div style={{ marginTop: 8 }}>
                <VersionPreview versionId={latest.id} />
              </div>
            </section>
          )}
          {latest && (
            <section>
              <ClassificationSection versionId={latest.id} onDone={reload} />
            </section>
          )}
          <section>
            <Typography.Text strong>版本历史</Typography.Text>
            <div style={{ marginTop: 8 }}>
              {(detail.versions ?? []).map((v) => (
                <div
                  key={v.id}
                  style={{
                    padding: "8px 0",
                    borderTop: "1px solid var(--eco-border)",
                  }}
                >
                  <Typography.Text>
                    第 {v.version_number} 版 · {businessStageOf(v)}
                  </Typography.Text>
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {formatDateTime(v.created_at)}
                      {v.reason_code ? ` · ${reasonCopy(v.reason_code)}` : ""}
                    </Typography.Text>
                  </div>
                  {v.id !== latest?.id && v.allowed_actions.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      <VersionActions version={v} onDone={reload} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </Drawer>
  );
}

// —— 面板主体 ——

export default function MaterialPanel({
  scope,
  clientId,
}: {
  scope: "shared" | "client";
  clientId?: string;
}) {
  const api = useApi();
  const { getAccessToken } = useAuth();
  const narrow = useNarrow();
  const [rows, setRows] = useState<MaterialItem[] | null>(null);
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [form] = Form.useForm<{ name: string }>();
  const [file, setFile] = useState<File | null>(null);
  const [openDocId, setOpenDocId] = useState<string | null>(null);
  // 上传幂等键：一次未知结果期间保持不变；成功/冲突/重开流程才更新
  const uploadKeyRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    // 域/客户变化：先清旧状态再加载。
    setRows(null);
    setDocs(null);
    setError(null);
    if (isMockData) {
      const load =
        scope === "shared" ? api.listSharedMaterials() : api.listClientMaterials(clientId!);
      load
        .then((items) => {
          if (active) setRows(items);
        })
        .catch((e) => {
          if (active) setError(e);
        });
    } else {
      getIngestionCapabilities(getAccessToken())
        .then((caps) => {
          if (active) setCapabilities(caps);
        })
        .catch(() => {
          // capabilities 不可得 → 不提供可执行按钮（fail-closed）
          if (active) setCapabilities(null);
        });
      listIngestionDocuments(getAccessToken(), {
        scopeKind: scope === "shared" ? "service_provider" : "client",
        clientAccountId: scope === "client" ? clientId : undefined,
        limit: 100,
      })
        .then((collection) => {
          assertScopePure(collection, scope, clientId);
          if (active) setDocs(collection.items);
        })
        .catch((e) => {
          if (active) setError(toApiError(e));
        });
    }
    return () => {
      active = false;
    };
  }, [api, getAccessToken, scope, clientId, nonce]);

  // 活动状态静默刷新：不清空现有列表，不显示加载指示
  useEffect(() => {
    if (isMockData || !docs) return;
    if (!docs.some((d) => ACTIVE_STAGES.has(stageOf(d)))) return;
    const timer = setTimeout(() => {
      listIngestionDocuments(getAccessToken(), {
        scopeKind: scope === "shared" ? "service_provider" : "client",
        clientAccountId: scope === "client" ? clientId : undefined,
        limit: 100,
      })
        .then((collection) => {
          assertScopePure(collection, scope, clientId);
          setDocs(collection.items);
        })
        .catch(() => {
          // 静默刷新失败不打断当前视图，下一轮继续
        });
    }, 5000);
    return () => clearTimeout(timer);
  }, [docs, getAccessToken, scope, clientId]);

  const openUpload = () => {
    uploadKeyRef.current = crypto.randomUUID();
    setUploadOpen(true);
  };

  const upload = useCallback(async () => {
    if (!file) return;
    const values = await form.validateFields();
    setUploading(true);
    try {
      if (isMockData) {
        await api.uploadMaterial({
          file,
          name: values.name || file.name,
          scope,
          clientId,
        });
      } else {
        await createIngestionDocument(
          getAccessToken(),
          values.name || file.name,
          file,
          uploadKeyRef.current ?? crypto.randomUUID(),
          undefined,
          "unknown",
          {
            kind: scope === "shared" ? "service_provider" : "client",
            client_account_id: scope === "client" ? (clientId ?? null) : null,
          },
        );
      }
      // 只陈述事实，不伪造进度百分比
      message.success("文件已接收，正在处理");
      uploadKeyRef.current = null; // 明确成功：允许下次生成新键
      setUploadOpen(false);
      setFile(null);
      form.resetFields();
      setNonce((n) => n + 1);
    } catch (e) {
      const code = (e as { code?: string })?.code;
      if (code === "IDEMPOTENCY_CONFLICT") {
        // 明确冲突：更换键并允许重试
        uploadKeyRef.current = crypto.randomUUID();
        message.error("请求标识冲突，请重试");
      } else {
        // 未知结果：保持同一幂等键
        message.error(isMockData ? "上传失败，请重试" : userFacingIngestionError(e));
      }
    } finally {
      setUploading(false);
    }
  }, [api, file, form, scope, clientId, getAccessToken]);

  if (error) {
    return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  }

  // 无权限或扫描不可用：不显示可执行按钮
  const uploadAllowed =
    isMockData ||
    (capabilities !== null &&
      capabilities.upload_enabled &&
      capabilities.scanner.state === "ready");
  const uploadBlockReason = !isMockData && capabilities
    ? !capabilities.upload_enabled
      ? reasonCopy(capabilities.disabled_reason_code ?? "SCAN_ENGINE_UNAVAILABLE")
      : capabilities.scanner.state !== "ready"
        ? "安全检查暂不可用，上传已暂停"
        : null
    : null;

  const uploadButton = uploadAllowed ? (
    <Button type="primary" onClick={openUpload}>
      {scope === "shared" ? "上传共享材料" : "上传指定客户材料"}
    </Button>
  ) : null;

  function uploadModal() {
    return (
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
    );
  }

  // —— 演示环境：简表走查 ——
  if (isMockData) {
    return (
      <div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          {uploadButton}
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
                <StatusDot
                  tone={
                    { processing: "processing", ready: "success", blocked: "warning", failed: "danger" }[
                      status
                    ] as StatusTone
                  }
                  label={MATERIAL_STATUS_LABEL[status]}
                />
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
        {uploadModal()}
      </div>
    );
  }

  // —— 正式 HTTP ——
  const counts = { total: docs?.length ?? 0, processing: 0, pendingConfirm: 0, failed: 0, done: 0 };
  for (const d of docs ?? []) {
    const stage = stageOf(d);
    if (ACTIVE_STAGES.has(stage)) counts.processing += 1;
    else if (stage === "待确认") counts.pendingConfirm += 1;
    else if (stage === "处理失败") counts.failed += 1;
    else if (stage === "入库处理完成") counts.done += 1;
  }

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        {docs === null
          ? "正在加载材料…"
          : `共 ${counts.total} 份 · 处理中 ${counts.processing} · 待确认 ${counts.pendingConfirm} · 失败 ${counts.failed} · 入库处理完成 ${counts.done}`}
        {uploadBlockReason ? `（${uploadBlockReason}）` : ""}
      </Typography.Paragraph>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        {uploadButton}
      </div>
      {narrow ? (
        docs === null ? (
          <Spin style={{ display: "block", margin: "48px auto" }} />
        ) : docs.length === 0 ? (
          <Typography.Text type="secondary">
            {scope === "shared" ? "暂无共享材料" : "暂无指定客户材料"}
          </Typography.Text>
        ) : (
          <div>
            {docs.map((d) => (
              <div key={d.id} className="client-mobile-item">
                <Button
                  type="link"
                  style={{ padding: 0, fontSize: 15 }}
                  onClick={() => setOpenDocId(d.id)}
                >
                  {d.display_name}
                </Button>
                <div className="client-mobile-meta">
                  <StatusDot tone={STAGE_TONE[stageOf(d)]} label={stageOf(d)} />
                  <span style={{ marginLeft: 8 }}>
                    {MATERIAL_KIND_LABEL[d.declared_material_kind]} · 第 {d.version_count} 版 ·{" "}
                    {formatDateTime(d.created_at)}
                  </span>
                </div>
                {d.latest_version?.reason_code && (
                  <div className="client-mobile-meta">
                    {reasonCopy(d.latest_version.reason_code)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      ) : (
        <Table<DocumentSummary>
          rowKey="id"
          loading={docs === null}
          dataSource={docs ?? []}
          pagination={false}
          locale={{ emptyText: scope === "shared" ? "暂无共享材料" : "暂无指定客户材料" }}
          columns={[
            {
              title: "文件名",
              dataIndex: "display_name",
              render: (name: string, row) => (
                <Button type="link" style={{ padding: 0 }} onClick={() => setOpenDocId(row.id)}>
                  {name}
                </Button>
              ),
            },
            {
              title: "分类",
              dataIndex: "declared_material_kind",
              width: 100,
              render: (kind: MaterialKind) => MATERIAL_KIND_LABEL[kind] ?? "未分类",
            },
            { title: "版本", dataIndex: "version_count", width: 70 },
            {
              title: "上传时间",
              dataIndex: "created_at",
              width: 150,
              render: (iso: string) => (
                <Typography.Text type="secondary">{formatDateTime(iso)}</Typography.Text>
              ),
            },
            {
              title: "业务状态",
              key: "stage",
              render: (_, row) => (
                <span>
                  <StatusDot tone={STAGE_TONE[stageOf(row)]} label={stageOf(row)} />
                  {row.latest_version?.reason_code && (
                    <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      {reasonCopy(row.latest_version.reason_code)}
                    </Typography.Text>
                  )}
                </span>
              ),
            },
            {
              title: "操作",
              key: "actions",
              width: 90,
              render: (_, row) => (
                <Button type="link" style={{ padding: 0 }} onClick={() => setOpenDocId(row.id)}>
                  详情
                </Button>
              ),
            },
          ]}
        />
      )}
      {openDocId && (
        <DocumentDrawer documentId={openDocId} onClose={() => setOpenDocId(null)} />
      )}
      {uploadModal()}
    </div>
  );
}
