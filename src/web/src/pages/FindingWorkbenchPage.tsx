import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import { listFindings } from "../p2FindingsApi";
import type {
  Finding,
  FindingCollection,
  FindingScope,
} from "../p2FindingsApi";

interface FindingWorkbenchPageProps {
  scope: FindingScope;
}

const EMPTY_COLLECTION: FindingCollection = {
  items: [],
  allowed_actions: [],
};

const PAGE_COPY: Record<FindingScope, { title: string; subtitle: string }> = {
  all: {
    title: "问题整改看板",
    subtitle: "集中查看现场问题、整改进度和复核状态",
  },
  rectification: {
    title: "企业整改",
    subtitle: "处理待整改、被退回和需要重新提交的问题",
  },
  review: {
    title: "顾问复核",
    subtitle: "查看待复核整改并作出通过或退回决定",
  },
};

function formatDateTime(value: string | null): string {
  if (!value) return "未设置";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function shortId(value: string | null): string {
  if (!value) return "未指定";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function severityColor(severity: string): string {
  if (severity === "critical") return "red";
  if (severity === "high") return "orange";
  if (severity === "medium") return "gold";
  return "blue";
}

function statusColor(status: string): string {
  if (["passed", "closed"].includes(status)) return "green";
  if (status === "rejected") return "red";
  if (["submitted", "reviewing"].includes(status)) return "purple";
  if (status === "rectifying") return "blue";
  return "default";
}

export default function FindingWorkbenchPage({ scope }: FindingWorkbenchPageProps) {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const caseId = searchParams.get("caseId");
  const [collection, setCollection] = useState<FindingCollection>(EMPTY_COLLECTION);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setCollection(EMPTY_COLLECTION);
      setError("请先在顶部选择企业");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setCollection(await listFindings(getAccessToken(), scope, caseId));
    } catch (reason) {
      setCollection(EMPTY_COLLECTION);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [caseId, getAccessToken, scope]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const canCreate =
    scope === "all" && collection.allowed_actions.includes("create");
  const detailScope = scope === "all" ? "" : `?scope=${scope}`;
  const createQuery = caseId ? `?caseId=${encodeURIComponent(caseId)}` : "";

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {PAGE_COPY[scope].title}
          </Typography.Title>
          <Typography.Text type="secondary">
            {PAGE_COPY[scope].subtitle}
          </Typography.Text>
        </div>
        {canCreate && (
          <Button
            type="primary"
            onClick={() => navigate(`/findings/new${createQuery}`)}
          >
            登记问题
          </Button>
        )}
      </Space>

      {caseId && (
        <Alert
          type="info"
          showIcon
          message="当前仅显示所选服务任务的问题"
          action={
            <Space wrap>
              <Button onClick={() => navigate(`/service-cases/${caseId}`)}>
                返回服务任务
              </Button>
              <Button onClick={() => navigate("/findings")}>查看全部</Button>
            </Space>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      {error && (
        <Alert
          type="error"
          showIcon
          message="问题列表加载失败"
          description={error}
          action={<Button onClick={() => void refresh()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin tip="正在加载问题整改任务" />
        </div>
      ) : collection.items.length === 0 && !error ? (
        <Empty description="当前没有需要处理的问题">
          {canCreate && (
            <Button
              type="primary"
              onClick={() => navigate(`/findings/new${createQuery}`)}
            >
              登记第一个问题
            </Button>
          )}
        </Empty>
      ) : (
        <Table<Finding>
          rowKey="id"
          dataSource={collection.items}
          scroll={{ x: 900 }}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          columns={[
            {
              title: "问题",
              dataIndex: "title",
              fixed: "left",
              width: 260,
              render: (value: string, finding) => (
                <Button
                  type="link"
                  onClick={() => navigate(`/findings/${finding.id}${detailScope}`)}
                >
                  {value}
                </Button>
              ),
            },
            {
              title: "严重程度",
              dataIndex: "severity",
              width: 120,
              render: (value: string) => (
                <Tag color={severityColor(value)}>{value}</Tag>
              ),
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 130,
              render: (value: string) => (
                <Tag color={statusColor(value)}>{value}</Tag>
              ),
            },
            {
              title: "责任人",
              dataIndex: "responsible_user_id",
              width: 140,
              render: shortId,
            },
            {
              title: "整改截止",
              dataIndex: "due_at",
              width: 200,
              render: formatDateTime,
            },
          ]}
        />
      )}
    </div>
  );
}
