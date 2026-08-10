import { Card, Empty, Space, Tag, Timeline, Typography } from "antd";
import type { BusinessTimelineItem } from "../p2Api";

interface BusinessTimelineProps {
  items: BusinessTimelineItem[];
}

const EVENT_LABELS: Record<string, string> = {
  "service_case.create": "创建服务任务",
  "service_case.update": "更新服务任务",
  "service_case.close": "关闭服务任务",
  "service_assignment.create": "分配执行人员",
  "service_assignment.accept": "接受任务分配",
  "service_assignment.reject": "拒绝任务分配",
  "service_assignment.revoke": "撤销任务分配",
  "site_visit.create": "安排现场服务",
  "site_visit.planned": "安排现场服务",
  "site_visit.update": "更新现场服务计划",
  "site_visit.rescheduled": "更新现场服务计划",
  "site_visit.start": "开始现场服务",
  "site_visit.started": "开始现场服务",
  "site_visit.complete": "完成现场服务",
  "site_visit.completed": "完成现场服务",
  "finding.create": "登记现场问题",
  "finding.start_rectification": "开始整改",
  "corrective_action.create": "提交整改",
  "finding.start_review": "开始复核",
  "finding.review.passed": "复核通过",
  "finding.review.rejected": "退回整改",
  "finding.close": "关闭问题",
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function shortId(value: string | null | undefined): string {
  if (!value) return "系统";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export default function BusinessTimeline({ items }: BusinessTimelineProps) {
  return (
    <Card title="业务时间线">
      {items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无业务事件" />
      ) : (
        <Timeline
          items={items.map((item) => ({
            children: (
              <Space direction="vertical" size={2}>
                <Space wrap size="small">
                  <Typography.Text strong>
                    {EVENT_LABELS[item.event_type] ?? item.event_type}
                  </Typography.Text>
                  {item.subject_type && <Tag>{item.subject_type}</Tag>}
                </Space>
                <Typography.Text type="secondary">
                  {formatDateTime(item.occurred_at)} · 操作人 {shortId(item.actor_user_id)}
                </Typography.Text>
              </Space>
            ),
          }))}
        />
      )}
    </Card>
  );
}
