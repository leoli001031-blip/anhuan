import { Descriptions, Empty, Typography } from "antd";
import type { LimitedMetricObject, LimitedMetricValue } from "../types";

const BLOCKED_KEY = /(text|content|body|prompt|question|answer|filename|file_name|path|url|token|email|phone|object)/i;

function safeValue(value: LimitedMetricValue): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "—";
  return value.length <= 96 ? value : value.slice(0, 93) + "…";
}

export default function LimitedMetrics({ metrics }: { metrics: LimitedMetricObject }) {
  const entries = Object.entries(metrics)
    .filter(([key, value]) => !BLOCKED_KEY.test(key) && ["string", "number", "boolean"].includes(typeof value))
    .sort(([left], [right]) => left.localeCompare(right));
  if (entries.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无可展示的有限指标" />;
  }
  return (
    <Descriptions bordered size="small" column={1}>
      {entries.map(([key, value]) => (
        <Descriptions.Item key={key} label={key.replaceAll("_", " ")}>
          <Typography.Text code={typeof value === "string"}>{safeValue(value)}</Typography.Text>
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}
