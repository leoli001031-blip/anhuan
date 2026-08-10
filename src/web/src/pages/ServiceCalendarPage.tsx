import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Grid,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import { getServiceCalendar } from "../p2WorkbenchApi";
import type {
  CalendarItemType,
  ServiceCalendarItem,
} from "../p2WorkbenchApi";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

const TYPE_LABELS: Record<CalendarItemType, string> = {
  case: "服务任务",
  visit: "现场服务",
  finding_deadline: "整改截止",
};

const TYPE_COLORS: Record<CalendarItemType, string> = {
  case: "blue",
  visit: "purple",
  finding_deadline: "orange",
};

function localDateKey(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function itemTarget(item: ServiceCalendarItem): string | null {
  if (item.finding_id) return `/findings/${item.finding_id}`;
  if (item.service_case_id) return `/service-cases/${item.service_case_id}`;
  return null;
}

function monthGridRange(month: Date): { start: Date; end: Date; days: Date[] } {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - mondayOffset);
  start.setHours(0, 0, 0, 0);
  const days = Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return day;
  });
  const end = new Date(start);
  end.setDate(start.getDate() + 42);
  return { start, end, days };
}

export default function ServiceCalendarPage() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const desktop = Boolean(screens.md);
  const [month, setMonth] = useState(
    () => new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  );
  const [items, setItems] = useState<ServiceCalendarItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const range = useMemo(() => monthGridRange(month), [month]);

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
      setItems(
        await getServiceCalendar(
          getAccessToken(),
          range.start.toISOString(),
          range.end.toISOString(),
        ),
      );
    } catch (reason) {
      setItems([]);
      setError(String(reason));
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, range.end, range.start]);

  useEffect(() => {
    void refresh();
    const handleTenantChange = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [refresh]);

  const itemsByDay = useMemo(() => {
    const grouped = new Map<string, ServiceCalendarItem[]>();
    for (const item of items) {
      const key = localDateKey(item.start_at);
      grouped.set(key, [...(grouped.get(key) ?? []), item]);
    }
    for (const dayItems of grouped.values()) {
      dayItems.sort((a, b) => a.start_at.localeCompare(b.start_at));
    }
    return grouped;
  }, [items]);

  const agendaDays = useMemo(
    () => Array.from(itemsByDay.entries()).sort(([left], [right]) => left.localeCompare(right)),
    [itemsByDay],
  );

  const changeMonth = (offset: number) => {
    setMonth((current) => new Date(current.getFullYear(), current.getMonth() + offset, 1));
  };

  return (
    <div style={{ textAlign: "left" }}>
      <Space
        align="center"
        wrap
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>
            服务日历
          </Typography.Title>
          <Typography.Text type="secondary">
            服务计划、现场执行和整改截止日期统一查看
          </Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => changeMonth(-1)}>上个月</Button>
          <Button
            onClick={() =>
              setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))
            }
          >
            今天
          </Button>
          <Button onClick={() => changeMonth(1)}>下个月</Button>
        </Space>
      </Space>

      <Typography.Title level={4} style={{ textAlign: "center" }}>
        {month.getFullYear()} 年 {month.getMonth() + 1} 月
      </Typography.Title>

      {error && (
        <Alert
          type="error"
          showIcon
          message="服务日历加载失败"
          description={error}
          action={<Button onClick={() => void refresh()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {loading ? (
        <div style={{ padding: 64, textAlign: "center" }}>
          <Spin tip="正在加载日历" />
        </div>
      ) : items.length === 0 && !error ? (
        <Card>
          <Empty description="当前月份没有服务或整改事项" />
        </Card>
      ) : desktop ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
            gap: 1,
            background: "#d9d9d9",
            border: "1px solid #d9d9d9",
          }}
        >
          {WEEKDAYS.map((weekday) => (
            <div
              key={weekday}
              style={{ background: "#fafafa", padding: 8, textAlign: "center" }}
            >
              <Typography.Text strong>周{weekday}</Typography.Text>
            </div>
          ))}
          {range.days.map((day) => {
            const key = localDateKey(day);
            const dayItems = itemsByDay.get(key) ?? [];
            const inMonth = day.getMonth() === month.getMonth();
            return (
              <div
                key={key}
                style={{
                  minHeight: 128,
                  padding: 8,
                  background: inMonth ? "#fff" : "#fafafa",
                  opacity: inMonth ? 1 : 0.65,
                  overflow: "hidden",
                }}
              >
                <Typography.Text strong={localDateKey(new Date()) === key}>
                  {day.getDate()}
                </Typography.Text>
                <Space direction="vertical" size={4} style={{ width: "100%", marginTop: 6 }}>
                  {dayItems.slice(0, 3).map((item) => {
                    const target = itemTarget(item);
                    return (
                      <div
                        key={`${item.item_type}:${item.id}`}
                        onClick={() => target && navigate(target)}
                        style={{
                          cursor: target ? "pointer" : "default",
                          padding: "3px 5px",
                          borderRadius: 4,
                          background: "#f0f5ff",
                          fontSize: 12,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={item.title}
                      >
                        {formatTime(item.start_at)} {item.title}
                      </div>
                    );
                  })}
                  {dayItems.length > 3 && (
                    <Typography.Text type="secondary">
                      另有 {dayItems.length - 3} 项
                    </Typography.Text>
                  )}
                </Space>
              </div>
            );
          })}
        </div>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {agendaDays.map(([date, dayItems]) => (
            <Card key={date} size="small" title={date}>
              <Space direction="vertical" size="small" style={{ width: "100%" }}>
                {dayItems.map((item) => {
                  const target = itemTarget(item);
                  return (
                    <Card
                      key={`${item.item_type}:${item.id}`}
                      size="small"
                      onClick={() => target && navigate(target)}
                      style={{ cursor: target ? "pointer" : "default" }}
                    >
                      <Space
                        align="center"
                        wrap
                        style={{ width: "100%", justifyContent: "space-between" }}
                      >
                        <div>
                          <Typography.Text strong>{item.title}</Typography.Text>
                          <br />
                          <Typography.Text type="secondary">
                            {formatTime(item.start_at)}
                          </Typography.Text>
                        </div>
                        <Tag color={TYPE_COLORS[item.item_type]}>
                          {TYPE_LABELS[item.item_type]}
                        </Tag>
                      </Space>
                    </Card>
                  );
                })}
              </Space>
            </Card>
          ))}
        </Space>
      )}
    </div>
  );
}
