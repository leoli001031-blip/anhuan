// 客户端报告列表：仅本企业已发布（published + artifact_ready）版本。
// 排版优先：标题 + 一行元信息 + 分隔线，无卡片瀑布。
import { useEffect, useState } from "react";
import { Spin, Typography } from "antd";
import { Link } from "react-router-dom";
import { useApi } from "../../adapters";
import type { PublishedReportSummaryV1 } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";

export default function ReportListPage() {
  const api = useApi();
  const [reports, setReports] = useState<PublishedReportSummaryV1[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .listPublishedReports()
      .then((rows) => {
        if (active) setReports(rows);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, nonce]);

  return (
    <div className="reading-column">
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        分析报告
      </Typography.Title>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      ) : reports === null ? (
        <Spin style={{ display: "block", margin: "48px auto" }} />
      ) : reports.length === 0 ? (
        <Typography.Paragraph type="secondary">
          暂无已发布的分析报告。报告发布后将会出现在这里。
        </Typography.Paragraph>
      ) : (
        <div>
          {reports.map((r) => (
            <div
              key={r.report_id}
              style={{
                padding: "20px 0",
                borderTop: "1px solid var(--eco-border)",
              }}
            >
              <Link
                to={`/portal/reports/${r.report_id}`}
                style={{ fontSize: 16, color: "var(--eco-text)" }}
              >
                <Typography.Text strong style={{ fontSize: 16, color: "inherit" }}>
                  {r.title}
                </Typography.Text>
              </Link>
              <div style={{ marginTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  {formatDateTime(r.published_at)} 发布 · 第 {r.version_number} 版
                </Typography.Text>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
