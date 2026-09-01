// 运营台 · 客户服务日历（/console/clients/:clientId/calendar）。
// 仅由已客户隔离的服务事项推导开始/截止节点，不依赖 legacy tenant 选择器。
import { useEffect, useMemo, useState } from "react";
import { Button, Empty, Spin, Typography } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../../auth/OidcProvider";
import ErrorState from "../../components/ErrorState";
import { itemStatusLabel } from "../../components/ServiceItemsShared";
import type { ServiceCase } from "../../p2Api";
import { listClientServiceCases } from "../../p2Api";
import ClientShell from "./ClientShell";
import { useNarrow } from "./useNarrow";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

interface CalendarEntry {
  key: string;
  title: string;
  status: string;
  at: Date;
  kind: "start" | "end" | "single";
}

interface ClientCalendarState {
  contextId: string;
  items: ServiceCase[] | null;
  error: unknown;
}

function startOfMonth(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

function toDate(value: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function dateKey(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthDays(month: Date): Date[] {
  const first = startOfMonth(month);
  const mondayOffset = (first.getDay() + 6) % 7;
  const firstCell = new Date(first);
  firstCell.setDate(first.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(firstCell);
    day.setDate(firstCell.getDate() + index);
    return day;
  });
}

function calendarEntries(items: ServiceCase[]): CalendarEntry[] {
  return items.flatMap((item, itemIndex) => {
    const start = toDate(item.planned_start_at);
    const end = toDate(item.planned_end_at);
    if (!start && !end) return [];

    const sameDay = start && end && dateKey(start) === dateKey(end);
    if (sameDay && start) {
      return [{
        key: `${itemIndex}-single`,
        title: item.title,
        status: item.status,
        at: start,
        kind: "single" as const,
      }];
    }

    const entries: CalendarEntry[] = [];
    if (start) {
      entries.push({
        key: `${itemIndex}-start`,
        title: item.title,
        status: item.status,
        at: start,
        kind: "start",
      });
    }
    if (end) {
      entries.push({
        key: `${itemIndex}-end`,
        title: item.title,
        status: item.status,
        at: end,
        kind: "end",
      });
    }
    return entries;
  });
}

function entryPrefix(kind: CalendarEntry["kind"]): string {
  if (kind === "start") return "开始";
  if (kind === "end") return "截止";
  return "计划";
}

function formatAgendaDate(value: Date): string {
  return value.toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  });
}

export default function ClientServiceCalendarPage() {
  const { clientId = "" } = useParams();
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const narrow = useNarrow();
  const [month, setMonth] = useState(() => startOfMonth(new Date()));
  const [calendarState, setCalendarState] = useState<ClientCalendarState>(() => ({
    contextId: clientId,
    items: null,
    error: null,
  }));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    const requestClientId = clientId;
    setCalendarState({ contextId: requestClientId, items: null, error: null });
    listClientServiceCases(getAccessToken(), requestClientId)
      .then((collection) => {
        if (active) {
          setCalendarState({
            contextId: requestClientId,
            items: collection.items,
            error: null,
          });
        }
      })
      .catch((reason) => {
        if (active) {
          setCalendarState({ contextId: requestClientId, items: null, error: reason });
        }
      });
    return () => {
      active = false;
    };
  }, [clientId, getAccessToken, nonce]);

  const inCurrentContext = calendarState.contextId === clientId;
  const items = inCurrentContext ? calendarState.items : null;
  const error = inCurrentContext ? calendarState.error : null;
  const entries = useMemo(() => calendarEntries(items ?? []), [items]);
  const days = useMemo(() => monthDays(month), [month]);
  const entriesByDay = useMemo(() => {
    const grouped = new Map<string, CalendarEntry[]>();
    for (const entry of entries) {
      const key = dateKey(entry.at);
      grouped.set(key, [...(grouped.get(key) ?? []), entry]);
    }
    for (const dayEntries of grouped.values()) {
      dayEntries.sort((left, right) => left.at.getTime() - right.at.getTime());
    }
    return grouped;
  }, [entries]);
  const monthEntries = useMemo(
    () => entries
      .filter(
        (entry) =>
          entry.at.getFullYear() === month.getFullYear()
          && entry.at.getMonth() === month.getMonth(),
      )
      .sort((left, right) => left.at.getTime() - right.at.getTime()),
    [entries, month],
  );
  const unplanned = useMemo(
    () => (items ?? []).filter((item) => !toDate(item.planned_start_at) && !toDate(item.planned_end_at)),
    [items],
  );

  const goToServices = () => navigate(`/console/clients/${clientId}/services`);

  return (
    <ClientShell clientId={clientId}>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((value) => value + 1)} />
      ) : items === null ? (
        <Spin style={{ display: "block", margin: "48px auto" }} />
      ) : (
        <div className="client-calendar">
          <div className="client-calendar__header">
            <div>
              <Typography.Title level={4}>
                {month.getFullYear()} 年 {month.getMonth() + 1} 月
              </Typography.Title>
              <Typography.Text type="secondary">
                本月 {monthEntries.length} 个服务节点
                {unplanned.length > 0 ? ` · ${unplanned.length} 项待排期` : ""}
              </Typography.Text>
            </div>
            <div className="client-calendar__actions">
              <Button onClick={() => setMonth((value) => new Date(value.getFullYear(), value.getMonth() - 1, 1))}>
                上个月
              </Button>
              <Button onClick={() => setMonth(startOfMonth(new Date()))}>今天</Button>
              <Button onClick={() => setMonth((value) => new Date(value.getFullYear(), value.getMonth() + 1, 1))}>
                下个月
              </Button>
            </div>
          </div>

          {narrow ? (
            monthEntries.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本月暂无已排期服务" />
            ) : (
              <div className="client-calendar__agenda">
                {monthEntries.map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    className="client-calendar__agenda-row"
                    onClick={goToServices}
                  >
                    <span>{formatAgendaDate(entry.at)}</span>
                    <strong>{entry.title}</strong>
                    <small>{entryPrefix(entry.kind)} · {itemStatusLabel(entry.status)}</small>
                  </button>
                ))}
              </div>
            )
          ) : (
            <div className="client-calendar__grid">
              {WEEKDAYS.map((weekday) => (
                <div key={weekday} className="client-calendar__weekday">周{weekday}</div>
              ))}
              {days.map((day) => {
                const key = dateKey(day);
                const dayEntries = entriesByDay.get(key) ?? [];
                const inMonth = day.getMonth() === month.getMonth();
                const today = key === dateKey(new Date());
                return (
                  <div
                    key={key}
                    className={`client-calendar__day${inMonth ? "" : " client-calendar__day--muted"}`}
                  >
                    <span className={today ? "client-calendar__date client-calendar__date--today" : "client-calendar__date"}>
                      {day.getDate()}
                    </span>
                    {dayEntries.slice(0, 3).map((entry) => (
                      <button
                        key={entry.key}
                        type="button"
                        className={`client-calendar__entry client-calendar__entry--${entry.kind}`}
                        title={`${entryPrefix(entry.kind)}：${entry.title}`}
                        onClick={goToServices}
                      >
                        <span>{entryPrefix(entry.kind)}</span>
                        {entry.title}
                      </button>
                    ))}
                    {dayEntries.length > 3 && (
                      <small className="client-calendar__more">另有 {dayEntries.length - 3} 项</small>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {unplanned.length > 0 && (
            <section className="client-calendar__unplanned">
              <Typography.Title level={5}>待排期</Typography.Title>
              <Typography.Paragraph type="secondary">
                以下服务事项尚未设置开始或截止时间。
              </Typography.Paragraph>
              {unplanned.map((item, index) => (
                <button
                  key={`${index}-${item.title}`}
                  type="button"
                  className="client-calendar__unplanned-row"
                  onClick={goToServices}
                >
                  <span>{item.title}</span>
                  <small>{itemStatusLabel(item.status)}</small>
                </button>
              ))}
            </section>
          )}
        </div>
      )}
    </ClientShell>
  );
}
