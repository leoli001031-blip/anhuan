import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import { getWorkbenchOverview } from "../p2WorkbenchApi";
import type {
  WorkbenchItem,
  WorkbenchOverview,
  WorkbenchSection,
  WorkbenchView,
} from "../p2WorkbenchApi";

const VIEW_COPY: Record<WorkbenchView, { title: string; subtitle: string }> = {
  admin: {
    title: "管理工作台",
    subtitle: "查看服务执行、人员安排和问题整改全局进展",
  },
  executor: {
    title: "执行工作台",
    subtitle: "集中处理分配给我的服务、现场任务和待复核事项",
  },
  enterprise: {
    title: "企业工作台",
    subtitle: "查看本企业服务进展并处理整改任务",
  },
};

const METRIC_LABELS: Record<string, string> = {
  active_cases: "进行中服务",
  my_cases: "我的任务",
  upcoming_visits: "待开展现场服务",
  open_findings: "待处理问题",
  overdue_findings: "逾期问题",
  pending_rectifications: "待整改",
  pending_reviews: "待复核",
  unread_notifications: "未读提醒",
};

function formatDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function itemTarget(item: WorkbenchItem): string | null {
  if (item.finding_id) return `/findings/${item.finding_id}`;
  if (item.service_case_id) return `/service-cases/${item.service_case_id}`;
  return null;
}

function statusColor(status: string): string {
  if (["closed", "completed", "passed"].includes(status)) return "green";
  if (["rejected", "overdue"].includes(status)) return "red";
  if (["in_progress", "rectifying", "reviewing"].includes(status)) return "blue";
  return "default";
}

export default function WorkbenchPage() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [overview, setOverview] = useState<WorkbenchOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setOverview(null);
      setError("请先在顶部选择企业");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setOverview(await getWorkbenchOverview(getAccessToken()));
    } catch (reason) {
      setOverview(null);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const sections = useMemo<WorkbenchSection[]>(() => {
    if (!overview) return [];
    if (overview.sections) return overview.sections;
    return [
      {
        key: "service_cases",
        title: "服务任务",
        items: (overview.service_cases ?? []).map((item) => ({
          ...item,
          service_case_id: item.service_case_id ?? item.id,
        })),
      },
      {
        key: "upcoming_visits",
        title: "近期现场服务",
        items: (overview.upcoming_visits ?? []).map((item) => ({
          ...item,
          title: item.title || "现场服务",
        })),
      },
      {
        key: "findings",
        title: "问题整改",
        items: (overview.findings ?? []).map((item) => ({
          ...item,
          finding_id: item.finding_id ?? item.id,
        })),
      },
      {
        key: "reviews",
        title: "待复核",
        items: (overview.reviews ?? []).map((item) => ({
          ...item,
          finding_id: item.finding_id ?? item.id,
        })),
      },
    ].filter((section) => section.items.length > 0);
  }, [overview]);

  if (loading) {
    return (
      <div style={{ padding: 64, textAlign: "center" }}>
        <Spin tip="正在加载工作台" />
      </div>
    );
  }

  if (!overview) {
    return (
      <div style={{ textAlign: "left" }}>
        <Alert
          type="error"
          showIcon
          message="工作台加载失败"
          description={error ?? "暂无可用工作台"}
          action={<Button onClick={() => void refresh()}>重试</Button>}
        />
      </div>
    );
  }

  const copy = VIEW_COPY[overview.view] ?? VIEW_COPY.executor;
  const metrics = Object.entries(overview.metrics ?? {});

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 20 }}
      >
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>
            {copy.title}
          </Typography.Title>
          <Typography.Text type="secondary">{copy.subtitle}</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => navigate("/calendar")}>查看日历</Button>
          <Button type="primary" onClick={() => navigate("/service-cases")}>
            打开服务任务
          </Button>
        </Space>
      </Space>

      {error && (
        <Alert
          type="warning"
          showIcon
          message="工作台部分内容可能未更新"
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}

      {metrics.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          {metrics.map(([key, value]) => (
            <Col key={key} xs={12} sm={8} lg={6} xl={4}>
              <Card size="small">
                <Statistic title={METRIC_LABELS[key] ?? key} value={value} />
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {sections.length === 0 ? (
        <Card>
          <Empty description="当前没有待处理事项" />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {sections.map((section) => (
            <Col key={section.key} xs={24} lg={12} xl={8}>
              <Card title={section.title} style={{ height: "100%" }}>
                {section.items.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无事项" />
                ) : (
                  <Space direction="vertical" size="small" style={{ width: "100%" }}>
                    {section.items.map((item) => {
                      const target = itemTarget(item);
                      const date = formatDateTime(item.due_at ?? item.planned_start_at);
                      return (
                        <Card
                          key={`${section.key}:${item.id}`}
                          size="small"
                          onClick={() => target && navigate(target)}
                          style={{ cursor: target ? "pointer" : "default" }}
                        >
                          <Space
                            align="center"
                            wrap
                            style={{ width: "100%", justifyContent: "space-between" }}
                          >
                            <Typography.Text strong>{item.title}</Typography.Text>
                            <Space size="small" wrap>
                              {item.status && (
                                <Tag color={statusColor(item.status)}>{item.status}</Tag>
                              )}
                              {date && (
                                <Typography.Text type="secondary">{date}</Typography.Text>
                              )}
                            </Space>
                          </Space>
                        </Card>
                      );
                    })}
                  </Space>
                )}
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}
