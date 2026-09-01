// 服务事项共享呈现：类型、统一状态文案、负责人、期限、逾期、问题与整改、处理记录、时间线。
// 状态统一为「待处理→处理中→待确认→已完成」；动作只消费后端 allowed_actions。
import { Typography } from "antd";
import type { ServiceCase } from "../p2Api";
import { formatDateTime } from "./ReportDocument";

export type ItemStatusLabel = "待处理" | "处理中" | "待确认" | "已完成" | "已取消" | "状态待确认";

export function itemStatusLabel(status: string | undefined): ItemStatusLabel {
  switch (status) {
    case "open":
      return "待处理";
    case "planned":
    case "in_progress":
    case "accepted":
    case "rectification":
    case "in_rectification":
      return "处理中";
    case "review":
    case "in_review":
    case "pending_review":
      return "待确认";
    case "closed":
    case "completed":
      return "已完成";
    case "cancelled":
      return "已取消";
    default:
      // 未知状态不映射为正常流程状态
      return "状态待确认";
  }
}

export function isOverdue(dueAt: string | null | undefined, status: string | undefined): boolean {
  if (!dueAt) return false;
  const label = itemStatusLabel(status);
  if (label === "已完成" || label === "已取消") return false;
  return new Date(dueAt).getTime() < Date.now();
}

const EVENT_LABELS: Record<string, string> = {
  "service_case.create": "创建服务任务",
  "service_case.created": "创建服务任务",
  "service_case.update": "更新服务任务",
  "service_case.updated": "更新服务任务",
  "service_case.started": "开始服务任务",
  "service_case.close": "关闭服务任务",
  "service_case.closed": "关闭服务任务",
  "service_case.auto_completed": "自动完成服务任务",
  "service_assignment.create": "分配执行人员",
  "service_assignment.created": "分配执行人员",
  "service_assignment.accept": "接受任务分配",
  "service_assignment.accepted": "接受任务分配",
  "service_assignment.reject": "拒绝任务分配",
  "service_assignment.rejected": "拒绝任务分配",
  "service_assignment.revoke": "撤销任务分配",
  "service_assignment.revoked": "撤销任务分配",
  "site_visit.create": "安排现场服务",
  "site_visit.planned": "安排现场服务",
  "site_visit.update": "更新现场服务计划",
  "site_visit.rescheduled": "更新现场服务计划",
  "site_visit.start": "开始现场服务",
  "site_visit.started": "开始现场服务",
  "site_visit.complete": "完成现场服务",
  "site_visit.completed": "完成现场服务",
  "finding.create": "登记现场问题",
  "finding.created": "登记现场问题",
  "finding.updated": "更新整改问题",
  "finding.rectify": "开始整改",
  "finding.start_rectification": "开始整改",
  "corrective_action.create": "提交整改",
  "corrective_action.submitted": "提交整改",
  "finding.submit": "提交整改",
  "finding.start_review": "开始复核",
  "finding.review": "开始复核",
  "finding.review.passed": "复核通过",
  "finding.review_pass": "复核通过",
  "finding.review_passed": "复核通过",
  "finding.review.rejected": "退回整改",
  "finding.review_reject": "退回整改",
  "finding.review_rejected": "退回整改",
  "finding.close": "关闭问题",
  "finding.closed": "关闭问题",
};

// 时间线：只呈现业务事件与时间，不显示操作人 ID 等技术标识。
export function ServiceTimeline({ items }: { items: ServiceCase["timeline"] }) {
  if (!items || items.length === 0) {
    return <Typography.Text type="secondary">暂无处理记录</Typography.Text>;
  }
  return (
    <div>
      {[...items]
        .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
        .map((item) => (
          <div
            key={item.id}
            style={{ padding: "8px 0", borderTop: "1px solid var(--eco-border)" }}
          >
            <Typography.Text>
              {EVENT_LABELS[item.event_type] ?? "状态已更新"}
            </Typography.Text>
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {formatDateTime(item.occurred_at)}
              </Typography.Text>
            </div>
          </div>
        ))}
    </div>
  );
}
