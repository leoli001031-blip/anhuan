// 客户门户 · 服务事项（/portal/services）：本企业租户内的真实 P2 列表。
// 缺 client-safe 详情 DTO：不请求、不展示 description/findings/timeline 等内部详情（见 BLOCKED-2）。
// 状态统一文案；未知状态显示「状态待确认」；client 无任何写操作。
import { useEffect, useState } from "react";
import { Spin, Typography } from "antd";
import { isMockData } from "../../adapters";
import ErrorState from "../../components/ErrorState";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import { formatDateTime } from "../../components/ReportDocument";
import { isOverdue, itemStatusLabel } from "../../components/ServiceItemsShared";
import type { ItemStatusLabel } from "../../components/ServiceItemsShared";
import { useAuth } from "../../auth/OidcProvider";
import { listServiceCases } from "../../p2Api";
import type { ServiceCase } from "../../p2Api";

const SERVICE_TYPE_LABEL: Record<string, string> = {
  onsite: "现场服务",
  onsite_visit: "现场服务",
  visit: "现场服务",
  remote: "远程支持",
  review: "复核",
  audit: "审计支持",
};

function serviceTypeLabel(value: string): string {
  return SERVICE_TYPE_LABEL[value] ?? "服务事项";
}

const STATUS_TONE: Record<ItemStatusLabel, StatusTone> = {
  待处理: "neutral",
  处理中: "processing",
  待确认: "warning",
  已完成: "success",
  已取消: "neutral",
  状态待确认: "warning",
};

export default function PortalServicesPage() {
  const { getAccessToken } = useAuth();
  const [cases, setCases] = useState<ServiceCase[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setCases(null);
    setError(null);
    if (isMockData) {
      // 演示环境无 P2 合成数据：呈现真实空态而非编造。
      setCases([]);
      return;
    }
    listServiceCases(getAccessToken(), "all")
      .then((collection) => {
        if (active) setCases(collection.items);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, nonce]);

  if (error) {
    return (
      <div className="reading-column">
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      </div>
    );
  }

  return (
    <div className="reading-column">
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        服务事项
      </Typography.Title>
      {cases === null ? (
        <Spin style={{ display: "block", margin: "48px auto" }} />
      ) : cases.length === 0 ? (
        <Typography.Paragraph type="secondary">
          暂无服务事项。服务商安排服务后会显示在这里。
        </Typography.Paragraph>
      ) : (
        <div>
          {cases.map((item) => (
            <div
              key={item.id}
              style={{ padding: "16px 0", borderTop: "1px solid var(--eco-border)" }}
            >
              <Typography.Text style={{ fontSize: 15 }}>{item.title}</Typography.Text>
              <div style={{ marginTop: 4, fontSize: 13 }}>
                <Typography.Text type="secondary">
                  {serviceTypeLabel(item.service_type)} ·{" "}
                </Typography.Text>
                <StatusDot
                  tone={STATUS_TONE[itemStatusLabel(item.status)]}
                  label={itemStatusLabel(item.status)}
                />
                <Typography.Text type="secondary">
                  {" "}
                  · {item.assignments && item.assignments.length > 0 ? "已指派" : "待指派"}
                  {item.planned_end_at
                    ? ` · 期限 ${formatDateTime(item.planned_end_at)}`
                    : ""}
                  {isOverdue(item.planned_end_at, item.status) ? (
                    <Typography.Text type="danger"> · 已逾期</Typography.Text>
                  ) : null}
                </Typography.Text>
              </div>
            </div>
          ))}
        </div>
      )}
      <Typography.Paragraph type="secondary" style={{ marginTop: 24, fontSize: 13 }}>
        事项详情由服务商在服务过程中同步；如有疑问请联系服务商。
      </Typography.Paragraph>
    </div>
  );
}
