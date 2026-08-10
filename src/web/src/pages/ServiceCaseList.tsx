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
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import { listServiceCases } from "../p2Api";
import type { ServiceCase, ServiceCaseCollection } from "../p2Api";

interface ServiceCaseListProps {
  scope?: "all" | "mine";
}

const EMPTY_COLLECTION: ServiceCaseCollection = {
  items: [],
  allowed_actions: [],
};

function formatDateTime(value: string | null): string {
  if (!value) return "未安排";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function statusColor(status: string): string {
  if (["closed", "completed"].includes(status)) return "green";
  if (["cancelled", "rejected"].includes(status)) return "red";
  if (["in_progress", "active"].includes(status)) return "blue";
  return "default";
}

export default function ServiceCaseList({ scope = "all" }: ServiceCaseListProps) {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [collection, setCollection] = useState<ServiceCaseCollection>(EMPTY_COLLECTION);
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
      setCollection(await listServiceCases(getAccessToken(), scope));
    } catch (reason) {
      setCollection(EMPTY_COLLECTION);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, scope]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const canCreate =
    scope === "all" && collection.allowed_actions.includes("create");

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {scope === "mine" ? "我的任务" : "服务任务"}
          </Typography.Title>
          <Typography.Text type="secondary">
            {scope === "mine"
              ? "查看明确分配给我的服务任务"
              : "创建、安排并跟踪企业服务任务"}
          </Typography.Text>
        </div>
        {canCreate && (
          <Button type="primary" onClick={() => navigate("/service-cases/new")}>
            创建服务任务
          </Button>
        )}
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="服务任务加载失败"
          description={error}
          action={<Button onClick={() => void refresh()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin tip="正在加载服务任务" />
        </div>
      ) : collection.items.length === 0 && !error ? (
        <Empty
          description={scope === "mine" ? "目前没有分配给你的任务" : "尚未创建服务任务"}
        >
          {canCreate && (
            <Button type="primary" onClick={() => navigate("/service-cases/new")}>
              创建第一个服务任务
            </Button>
          )}
        </Empty>
      ) : (
        <Table<ServiceCase>
          rowKey="id"
          dataSource={collection.items}
          scroll={{ x: 820 }}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          columns={[
            {
              title: "任务名称",
              dataIndex: "title",
              fixed: "left",
              width: 240,
              render: (value: string, item) => (
                <Button type="link" onClick={() => navigate(`/service-cases/${item.id}`)}>
                  {value}
                </Button>
              ),
            },
            { title: "服务类型", dataIndex: "service_type", width: 160 },
            {
              title: "状态",
              dataIndex: "status",
              width: 120,
              render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
            },
            {
              title: "计划开始",
              dataIndex: "planned_start_at",
              width: 190,
              render: formatDateTime,
            },
            {
              title: "计划结束",
              dataIndex: "planned_end_at",
              width: 190,
              render: formatDateTime,
            },
          ]}
        />
      )}
    </div>
  );
}
