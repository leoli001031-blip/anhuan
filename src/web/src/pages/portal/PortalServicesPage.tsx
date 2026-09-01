// 客户门户 · 服务事项（/portal/services）：只消费 /service-cases/portal 安全摘要。
// 不请求、不持有 description/assignments/findings/timeline 等 provider 详情字段。
// 状态统一文案；未知状态显示「状态待确认」；client 无任何写操作。
import { useEffect, useState } from "react";
import { Spin, Typography } from "antd";
import ErrorState from "../../components/ErrorState";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import { formatDateTime } from "../../components/ReportDocument";
import { isOverdue, itemStatusLabel } from "../../components/ServiceItemsShared";
import type { ItemStatusLabel } from "../../components/ServiceItemsShared";
import { useAuth } from "../../auth/OidcProvider";
import { listPortalServiceCases } from "../../p2Api";
import type { PortalServiceCaseSummary } from "../../p2Api";

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
  const [cases, setCases] = useState<PortalServiceCaseSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setCases(null);
    setError(null);
    listPortalServiceCases(getAccessToken())
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
                  {" "}· {item.assigned ? "已指派" : "待指派"}
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
        此处仅展示已安全开放的服务摘要；如有疑问请联系服务商。
      </Typography.Paragraph>
    </div>
  );
}
