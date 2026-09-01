// 运营台 · 指定客户服务事项（/console/clients/:clientId/services）。
// 列表和创建都强制携带路由 clientId；不链接 legacy 详情，不使用 mock/fallback。
import { useEffect, useState } from "react";
import { Button, Drawer, Empty, Modal, Spin, Typography, message } from "antd";
import { useParams } from "react-router-dom";
import { useAuth } from "../../auth/OidcProvider";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import ServiceCaseForm from "../../components/ServiceCaseForm";
import {
  isOverdue,
  itemStatusLabel,
  ServiceTimeline,
} from "../../components/ServiceItemsShared";
import type { ItemStatusLabel } from "../../components/ServiceItemsShared";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import {
  createClientServiceCase,
  getClientServiceCase,
  listClientServiceCases,
} from "../../p2Api";
import type {
  ServiceCase,
  ServiceCaseCollection,
  ServiceCaseInput,
} from "../../p2Api";
import ClientShell from "./ClientShell";

const SERVICE_TYPE_LABEL: Record<string, string> = {
  onsite: "现场服务",
  onsite_visit: "现场服务",
  visit: "现场服务",
  remote: "远程支持",
  review: "复核",
  audit: "审计支持",
};

const STATUS_TONE: Record<ItemStatusLabel, StatusTone> = {
  待处理: "neutral",
  处理中: "processing",
  待确认: "warning",
  已完成: "success",
  已取消: "neutral",
  状态待确认: "warning",
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
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

interface ClientCollectionState {
  contextId: string;
  collection: ServiceCaseCollection | null;
  error: unknown;
}

interface ClientDetailSelection {
  contextId: string;
  caseId: string;
}

interface ClientDetailState {
  contextId: string;
  caseId: string | null;
  detail: ServiceCase | null;
  error: unknown;
}

function serviceTypeLabel(value: string): string {
  return SERVICE_TYPE_LABEL[value] ?? value;
}

export default function ClientServicesPage() {
  const { clientId = "" } = useParams();
  const { getAccessToken } = useAuth();
  const [collectionState, setCollectionState] = useState<ClientCollectionState>(() => ({
    contextId: clientId,
    collection: null,
    error: null,
  }));
  const [nonce, setNonce] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [detailSelection, setDetailSelection] = useState<ClientDetailSelection | null>(null);
  const [detailState, setDetailState] = useState<ClientDetailState>(() => ({
    contextId: clientId,
    caseId: null,
    detail: null,
    error: null,
  }));
  const [detailNonce, setDetailNonce] = useState(0);

  useEffect(() => {
    // 路由客户变化时丢弃上一客户的临时表单与详情选择。
    setCreateOpen(false);
    setDetailSelection(null);
  }, [clientId]);

  useEffect(() => {
    let active = true;
    const requestClientId = clientId;
    setCollectionState({ contextId: requestClientId, collection: null, error: null });
    listClientServiceCases(getAccessToken(), requestClientId)
      .then((next) => {
        if (active) {
          setCollectionState({
            contextId: requestClientId,
            collection: next,
            error: null,
          });
        }
      })
      .catch((reason) => {
        if (active) {
          setCollectionState({
            contextId: requestClientId,
            collection: null,
            error: reason,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [clientId, getAccessToken, nonce]);

  useEffect(() => {
    const selection = detailSelection;
    if (!selection || selection.contextId !== clientId) {
      setDetailState({ contextId: clientId, caseId: null, detail: null, error: null });
      return;
    }
    let active = true;
    const requestClientId = selection.contextId;
    const requestCaseId = selection.caseId;
    setDetailState({
      contextId: requestClientId,
      caseId: requestCaseId,
      detail: null,
      error: null,
    });
    getClientServiceCase(getAccessToken(), requestClientId, requestCaseId)
      .then((next) => {
        if (active) {
          setDetailState({
            contextId: requestClientId,
            caseId: requestCaseId,
            detail: next,
            error: null,
          });
        }
      })
      .catch((reason) => {
        if (active) {
          setDetailState({
            contextId: requestClientId,
            caseId: requestCaseId,
            detail: null,
            error: reason,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [clientId, detailNonce, detailSelection, getAccessToken]);

  const collectionInContext = collectionState.contextId === clientId;
  const collection = collectionInContext ? collectionState.collection : null;
  const error = collectionInContext ? collectionState.error : null;
  const activeSelection = detailSelection?.contextId === clientId
    ? detailSelection
    : null;
  const detailInContext = activeSelection !== null
    && detailState.contextId === clientId
    && detailState.caseId === activeSelection.caseId;
  const detail = detailInContext ? detailState.detail : null;
  const detailError = detailInContext ? detailState.error : null;

  const create = async (values: ServiceCaseInput) => {
    setCreating(true);
    try {
      await createClientServiceCase(getAccessToken(), clientId, values);
      message.success("服务事项已创建");
      setCreateOpen(false);
      setNonce((value) => value + 1);
    } catch {
      message.error("创建失败，请重试");
    } finally {
      setCreating(false);
    }
  };

  const canCreate = collection?.allowed_actions.includes("create") === true;

  return (
    <ClientShell clientId={clientId}>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((value) => value + 1)} />
      ) : (
        <>
          {collection && (
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                alignItems: "center",
                minHeight: 40,
                marginBottom: 12,
              }}
            >
              {canCreate ? (
                <Button type="primary" onClick={() => setCreateOpen(true)}>
                  新建服务事项
                </Button>
              ) : (
                <Typography.Text type="secondary" data-client-services-readonly="1">
                  只读：当前账号无创建服务事项权限
                </Typography.Text>
              )}
            </div>
          )}

          {collection === null ? (
            <Spin style={{ display: "block", margin: "48px auto" }} />
          ) : collection.items.length === 0 ? (
            <Typography.Text type="secondary">
              {canCreate ? "暂无服务事项，可在右上角新建" : "暂无服务事项"}
            </Typography.Text>
          ) : (
            <div>
              {collection.items.map((item) => {
                const statusLabel = itemStatusLabel(item.status);
                return (
                  <button
                    key={item.id}
                    type="button"
                    data-client-service-case="1"
                    className="client-service-row"
                    aria-label={`查看服务事项：${item.title}`}
                    onClick={() => setDetailSelection({ contextId: clientId, caseId: item.id })}
                  >
                    <div>
                      <Typography.Text style={{ fontSize: 15 }}>{item.title}</Typography.Text>
                      <div style={{ marginTop: 4, fontSize: 13 }}>
                        <Typography.Text type="secondary">
                          {serviceTypeLabel(item.service_type)} ·{" "}
                        </Typography.Text>
                        <StatusDot tone={STATUS_TONE[statusLabel]} label={statusLabel} />
                        {isOverdue(item.planned_end_at, item.status) && (
                          <Typography.Text type="danger"> · 已逾期</Typography.Text>
                        )}
                      </div>
                      <Typography.Text type="secondary" style={{ display: "block", marginTop: 4, fontSize: 13 }}>
                        计划：{item.planned_start_at ? formatDateTime(item.planned_start_at) : "未安排"}
                        {" → "}
                        {item.planned_end_at ? formatDateTime(item.planned_end_at) : "未安排"}
                      </Typography.Text>
                    </div>
                    <span className="client-service-row__action">查看</span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}

      <Modal
        open={createOpen}
        title="新建服务事项"
        footer={null}
        destroyOnHidden
        maskClosable={!creating}
        closable={!creating}
        onCancel={() => {
          if (!creating) setCreateOpen(false);
        }}
      >
        <ServiceCaseForm
          submitLabel="创建"
          submitting={creating}
          onSubmit={create}
          onCancel={() => {
            if (!creating) setCreateOpen(false);
          }}
        />
      </Modal>

      <Drawer
        className="client-service-drawer"
        open={activeSelection !== null}
        title={detail?.title ?? "服务事项详情"}
        width={560}
        destroyOnHidden
        onClose={() => setDetailSelection(null)}
      >
        {detailError ? (
          <ErrorState
            error={detailError}
            onRetry={() => setDetailNonce((value) => value + 1)}
          />
        ) : detail === null ? (
          <Spin style={{ display: "block", margin: "48px auto" }} />
        ) : (
          <div className="client-service-detail">
            <div className="client-service-detail__facts">
              <div>
                <Typography.Text type="secondary">服务类型</Typography.Text>
                <Typography.Text>{serviceTypeLabel(detail.service_type)}</Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">当前状态</Typography.Text>
                <StatusDot
                  tone={STATUS_TONE[itemStatusLabel(detail.status)]}
                  label={itemStatusLabel(detail.status)}
                />
              </div>
              <div>
                <Typography.Text type="secondary">计划开始</Typography.Text>
                <Typography.Text>
                  {detail.planned_start_at ? formatDateTime(detail.planned_start_at) : "未安排"}
                </Typography.Text>
              </div>
              <div>
                <Typography.Text type="secondary">计划截止</Typography.Text>
                <Typography.Text type={isOverdue(detail.planned_end_at, detail.status) ? "danger" : undefined}>
                  {detail.planned_end_at ? formatDateTime(detail.planned_end_at) : "未安排"}
                  {isOverdue(detail.planned_end_at, detail.status) ? " · 已逾期" : ""}
                </Typography.Text>
              </div>
            </div>

            <section className="client-service-detail__section">
              <Typography.Title level={5}>事项说明</Typography.Title>
              {detail.description ? (
                <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                  {detail.description}
                </Typography.Paragraph>
              ) : (
                <Typography.Text type="secondary">暂无事项说明</Typography.Text>
              )}
            </section>

            <section className="client-service-detail__section">
              <Typography.Title level={5}>整改问题</Typography.Title>
              {(detail.findings ?? []).length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前事项暂无整改问题" />
              ) : (
                <div className="client-service-detail__findings">
                  {(detail.findings ?? []).map((finding, index) => (
                    <div key={`${index}-${finding.title}`} className="client-service-detail__finding">
                      <Typography.Text strong>{finding.title}</Typography.Text>
                      <div>
                        <Typography.Text type="secondary">
                          {SEVERITY_LABEL[finding.severity] ?? "严重度待确认"}
                          {" · "}
                          {FINDING_STATUS_LABEL[finding.status] ?? "状态待确认"}
                          {" · "}
                          {finding.due_at ? `期限 ${formatDateTime(finding.due_at)}` : "未设期限"}
                        </Typography.Text>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="client-service-detail__section">
              <Typography.Title level={5}>处理记录</Typography.Title>
              <ServiceTimeline items={detail.timeline} />
            </section>
          </div>
        )}
      </Drawer>
    </ClientShell>
  );
}
