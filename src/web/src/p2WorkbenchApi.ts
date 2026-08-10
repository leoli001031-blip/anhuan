import { api } from "./api";

export type WorkbenchView = "admin" | "executor" | "enterprise";

export interface WorkbenchItem {
  id: string;
  title: string;
  status?: string;
  due_at?: string | null;
  planned_start_at?: string | null;
  service_case_id?: string;
  finding_id?: string;
}

export interface WorkbenchSection {
  key: string;
  title: string;
  items: WorkbenchItem[];
}

export interface WorkbenchOverview {
  view: WorkbenchView;
  metrics: Record<string, number>;
  service_cases?: WorkbenchItem[];
  findings?: WorkbenchItem[];
  upcoming_visits?: WorkbenchItem[];
  reviews?: WorkbenchItem[];
  sections?: WorkbenchSection[];
}

export type CalendarItemType =
  | "case"
  | "visit"
  | "finding_deadline";

export interface ServiceCalendarItem {
  id: string;
  item_type: CalendarItemType;
  title: string;
  start_at: string;
  end_at?: string | null;
  status?: string;
  service_case_id?: string | null;
  finding_id?: string | null;
}

export interface NotificationItem {
  id: string;
  event_type: string;
  subject_type: string;
  subject_id: string;
  created_at: string;
  read_at: string | null;
  service_case_id?: string | null;
  allowed_actions: string[];
}

export const NOTIFICATIONS_CHANGED_EVENT = "p2-notifications-changed";

export function getWorkbenchOverview(
  token: string | null,
): Promise<WorkbenchOverview> {
  return api<WorkbenchOverview>("/v1/workbench/overview", { token });
}

export async function getServiceCalendar(
  token: string | null,
  startAt: string,
  endAt: string,
): Promise<ServiceCalendarItem[]> {
  const query = new URLSearchParams({ start_at: startAt, end_at: endAt });
  const payload = await api<ServiceCalendarItem[] | { items: ServiceCalendarItem[] }>(
    `/v1/workbench/calendar?${query.toString()}`,
    { token },
  );
  return Array.isArray(payload) ? payload : (payload.items ?? []);
}

export async function getNotifications(
  token: string | null,
): Promise<NotificationItem[]> {
  const payload = await api<NotificationItem[] | { items: NotificationItem[] }>(
    "/v1/workbench/notifications?unread_only=false&limit=30",
    { token },
  );
  return Array.isArray(payload) ? payload : (payload.items ?? []);
}

export async function getUnreadNotificationCount(
  token: string | null,
): Promise<number> {
  const payload = await api<number | { unread_count: number }>(
    "/v1/workbench/notifications/unread-count",
    { token },
  );
  return typeof payload === "number" ? payload : payload.unread_count;
}

export function markNotificationRead(
  token: string | null,
  notificationId: string,
): Promise<NotificationItem> {
  return api<NotificationItem>(`/v1/workbench/notifications/${notificationId}/read`, {
    method: "POST",
    token,
  });
}
