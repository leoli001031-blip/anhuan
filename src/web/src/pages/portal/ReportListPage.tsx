// 客户端报告列表：仅本企业已发布（published + artifact_ready）版本。
// 排版优先：标题 + 一行元信息 + 分隔线，无卡片瀑布。
import { useEffect, useState } from "react";
import { Button, Spin, Typography } from "antd";
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
    <main className="portal-page portal-report-list">
      <Typography.Title level={1} className="portal-page__title">
        分析报告
      </Typography.Title>
      <Typography.Paragraph className="portal-page__subtitle">
        查看本企业已发布的资料分析结论与改善建议。
      </Typography.Paragraph>
      {error ? (
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      ) : reports === null ? (
        <Spin style={{ display: "block", margin: "48px auto" }} />
      ) : reports.length === 0 ? (
        <Typography.Paragraph type="secondary">
          暂无已发布的分析报告。报告发布后将会出现在这里。
        </Typography.Paragraph>
      ) : (
        <div className="portal-report-list__rows">
          {reports.map((r) => (
            <article key={r.report_id} className="portal-report-row">
              <div className="portal-report-row__body">
                <Link to={`/portal/reports/${r.report_id}`} className="portal-report-row__title">
                  {r.title}
                </Link>
                <div className="portal-report-row__meta">
                  <span className="portal-report-row__status">已发布</span>
                  <Typography.Text type="secondary">
                    {formatDateTime(r.published_at)} · 第 {r.version_number} 版
                  </Typography.Text>
                </div>
              </div>
              <Button type="primary">
                <Link to={`/portal/reports/${r.report_id}`}>查看报告</Link>
              </Button>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
