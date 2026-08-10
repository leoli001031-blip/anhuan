import { useCallback } from "react";
import {
  Alert,
  Button,
  Empty,
  Grid,
  List,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { useNavigate } from "react-router-dom";
import P4BoundaryBanner from "../components/P4BoundaryBanner";
import { useP4TenantQuery } from "../hooks/useP4TenantQuery";
import {
  dashboardViewCopy,
  formatP4DateTime,
  metricCopy,
  queueCopy,
} from "../reasonCopy";
import type { DashboardOverview, DashboardQueueItem } from "../types";
import { getRoleDashboard } from "../viewsReportsApi";

const EMPTY_DASHBOARD: DashboardOverview = {
  view: "enterprise",
  as_of: "",
  metrics: {},
  queues: {},
  allowed_actions: [],
};

function queueItemLabel(item: DashboardQueueItem): string {
  const labels: Record<string, string> = {
    service_case: "服务任务",
    site_visit: "现场服务",
    finding: "问题整改",
    report: "业务报告",
    business_report: "业务报告",
    crm_follow_up: "客户跟进",
    crm_account: "客户跟进",
  };
  const label = item.title ?? item.display_name ?? item.label ?? labels[item.kind] ?? "业务待办";
  return `${label} · ${item.id.slice(-8)}`;
}

function queueItemKind(item: DashboardQueueItem): string {
  return (item.kind ?? item.subject_type ?? item.item_type ?? "business_item").replaceAll("_", " ");
}

export default function RoleDashboardPage() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const load = useCallback(
    (token: string | null, signal: AbortSignal) => getRoleDashboard(token, signal),
    [],
  );
  const { data, loading, error, reload } = useP4TenantQuery(EMPTY_DASHBOARD, load);
  const metricEntries = Object.entries(data.metrics);
  const queueEntries = Object.entries(data.queues);
  const isEmpty = metricEntries.length === 0 && queueEntries.every(([, items]) => items.length === 0);

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        wrap
        align="center"
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Space wrap align="center">
            <Typography.Title level={3} style={{ margin: 0 }}>
              经营驾驶舱
            </Typography.Title>
            {!loading && <Tag color="blue">{dashboardViewCopy(data.view)}</Tag>}
          </Space>
          <Typography.Text type="secondary">
            当前企业业务队列与内部经营快照 · 截止 {formatP4DateTime(data.as_of)}
          </Typography.Text>
        </div>
        <Space wrap>
          {data.allowed_actions.includes("create_crm_account") && (
            <Button onClick={() => navigate("/crm")}>建立客户档案</Button>
          )}
          {data.allowed_actions.includes("create_report") && (
            <Button type="primary" onClick={() => navigate("/reports")}>建立业务报告</Button>
          )}
          <Button onClick={() => void reload()} disabled={loading}>刷新</Button>
        </Space>
      </Space>

      <P4BoundaryBanner />

      {error && (
        <Alert
          type="error"
          showIcon
          message="驾驶舱加载失败"
          description={error}
          action={<Button onClick={() => void reload()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ minHeight: 360, display: "grid", placeItems: "center" }}>
          <Spin tip="正在汇总当前企业经营视图" />
        </div>
      ) : isEmpty && !error ? (
        <Empty description="当前角色暂无经营指标或待办" />
      ) : (
        <Space direction="vertical" size={24} style={{ width: "100%" }}>
          {metricEntries.length > 0 && (
            <section aria-labelledby="p4-metrics-heading">
              <Typography.Title id="p4-metrics-heading" level={4}>关键指标</Typography.Title>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: screens.md ? "repeat(4, minmax(0, 1fr))" : "repeat(2, minmax(0, 1fr))",
                  borderTop: "1px solid #f0f0f0",
                  borderLeft: "1px solid #f0f0f0",
                }}
              >
                {metricEntries.map(([key, value]) => (
                  <div key={key} style={{ padding: screens.md ? 20 : 14, borderRight: "1px solid #f0f0f0", borderBottom: "1px solid #f0f0f0" }}>
                    <Statistic title={metricCopy(key)} value={value} />
                  </div>
                ))}
              </div>
            </section>
          )}

          {queueEntries.map(([key, items]) => (
            <section key={key} aria-labelledby={`p4-queue-${key}`}>
              <Space align="baseline">
                <Typography.Title id={`p4-queue-${key}`} level={4} style={{ marginBottom: 12 }}>
                  {queueCopy(key)}
                </Typography.Title>
                <Typography.Text type="secondary">{items.length} 项</Typography.Text>
              </Space>
              {items.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办" />
              ) : (
                <List
                  bordered
                  dataSource={items}
                  renderItem={(item) => (
                    <List.Item>
                      <div style={{ width: "100%", display: "flex", gap: 12, alignItems: screens.sm ? "center" : "flex-start", justifyContent: "space-between", flexDirection: screens.sm ? "row" : "column" }}>
                        <div style={{ minWidth: 0 }}>
                          <Typography.Text strong>{queueItemLabel(item)}</Typography.Text>
                          <div>
                            <Typography.Text type="secondary">{queueItemKind(item)}</Typography.Text>
                          </div>
                        </div>
                        <Space wrap size="small">
                          {item.status && <Tag>{item.status}</Tag>}
                          {item.due_at && <Typography.Text type="secondary">{formatP4DateTime(item.due_at)}</Typography.Text>}
                        </Space>
                      </div>
                    </List.Item>
                  )}
                />
              )}
            </section>
          ))}
        </Space>
      )}
    </div>
  );
}
