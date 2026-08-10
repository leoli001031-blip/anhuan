import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import {
  getNotifications,
  markNotificationRead,
  NOTIFICATIONS_CHANGED_EVENT,
} from "../p2WorkbenchApi";
import type { NotificationItem } from "../p2WorkbenchApi";

const EVENT_LABELS: Record<string, string> = {
  "service_case.create": "新的服务任务已创建",
  "service_case.created": "新的服务任务已创建",
  "service_assignment.create": "收到新的服务任务分配",
  "service_case.assigned": "收到新的服务任务分配",
  "service_case.close": "服务任务已关闭",
  "service_case.closed": "服务任务已关闭",
  "site_visit.create": "现场服务已安排",
  "site_visit.planned": "现场服务已安排",
  "site_visit.start": "现场服务已开始",
  "site_visit.started": "现场服务已开始",
  "site_visit.complete": "现场服务已完成",
  "site_visit.completed": "现场服务已完成",
  "finding.create": "新的现场问题已登记",
  "finding.created": "新的现场问题已登记",
  "finding.due_soon": "整改截止日期临近",
  "finding.overdue": "整改任务已逾期",
  "corrective_action.create": "整改已提交，等待复核",
  "finding.submitted": "整改已提交，等待复核",
  "finding.review.rejected": "整改被退回，需要重新处理",
  "finding.rejected": "整改被退回，需要重新处理",
  "finding.review.passed": "整改已通过复核",
  "finding.passed": "整改已通过复核",
};

const SUBJECT_LABELS: Record<string, string> = {
  service_case: "服务任务",
  site_visit: "现场服务",
  finding: "问题整改",
  corrective_action: "整改提交",
  finding_review: "复核事项",
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

export default function NotificationsPage() {
  const { getAccessToken } = useAuth();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [markingId, setMarkingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setItems([]);
      setError("请先在顶部选择企业");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setItems(await getNotifications(getAccessToken()));
    } catch (reason) {
      setItems([]);
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

  const visibleItems = useMemo(
    () => (showUnreadOnly ? items.filter((item) => !item.read_at) : items),
    [items, showUnreadOnly],
  );
  const unreadCount = items.filter((item) => !item.read_at).length;

  const markRead = async (item: NotificationItem) => {
    if (!item.allowed_actions.includes("mark_read")) return;
    setMarkingId(item.id);
    setError(null);
    try {
      await markNotificationRead(getAccessToken(), item.id);
      setItems((current) =>
        current.map((candidate) =>
          candidate.id === item.id
            ? {
                ...candidate,
                read_at: new Date().toISOString(),
                allowed_actions: candidate.allowed_actions.filter(
                  (action) => action !== "mark_read",
                ),
              }
            : candidate,
        ),
      );
      window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED_EVENT));
      message.success("提醒已标记为已读");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setMarkingId(null);
    }
  };

  return (
    <div style={{ maxWidth: 900, textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>
            站内提醒
          </Typography.Title>
          <Typography.Text type="secondary">
            最近30条服务、整改和复核提醒
          </Typography.Text>
        </div>
        <Space wrap>
          <Badge count={unreadCount} overflowCount={99}>
            <Tag color={unreadCount > 0 ? "blue" : "default"}>未读</Tag>
          </Badge>
          <Typography.Text>仅看未读</Typography.Text>
          <Switch checked={showUnreadOnly} onChange={setShowUnreadOnly} />
        </Space>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="提醒操作未完成"
          description={error}
          action={<Button onClick={() => void refresh()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ padding: 64, textAlign: "center" }}>
          <Spin tip="正在加载提醒" />
        </div>
      ) : visibleItems.length === 0 && !error ? (
        <Card>
          <Empty
            description={showUnreadOnly ? "没有未读提醒" : "目前没有站内提醒"}
          />
        </Card>
      ) : (
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          {visibleItems.map((item) => {
            const unread = !item.read_at;
            return (
              <Card
                key={item.id}
                size="small"
                style={{ borderLeft: unread ? "4px solid #1677ff" : undefined }}
              >
                <Space
                  align="center"
                  wrap
                  style={{ width: "100%", justifyContent: "space-between" }}
                >
                  <Space direction="vertical" size={3}>
                    <Space wrap size="small">
                      <Typography.Text strong={unread}>
                        {EVENT_LABELS[item.event_type] ?? item.event_type}
                      </Typography.Text>
                      <Tag>{SUBJECT_LABELS[item.subject_type] ?? item.subject_type}</Tag>
                      {unread && <Badge status="processing" text="未读" />}
                    </Space>
                    <Typography.Text type="secondary">
                      {formatDateTime(item.created_at)}
                    </Typography.Text>
                  </Space>
                  {item.allowed_actions.includes("mark_read") && (
                    <Button
                      size="small"
                      loading={markingId === item.id}
                      onClick={() => void markRead(item)}
                    >
                      标记已读
                    </Button>
                  )}
                </Space>
              </Card>
            );
          })}
        </Space>
      )}
    </div>
  );
}
