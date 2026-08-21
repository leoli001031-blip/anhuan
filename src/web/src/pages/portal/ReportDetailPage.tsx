// 客户端报告详情：白纸黑字的阅读页，左侧锚点目录，无任何管理操作。
// 未发布/已撤回/他企业 → 后端统一 404，前端呈现「内容不存在」。
import { useEffect, useState } from "react";
import { Col, Row, Spin, Typography } from "antd";
import { useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import type { PublishedReportDetailV1 } from "../../adapters/types";
import { SECTION_ORDER } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import ReportDocument, { formatDateTime } from "../../components/ReportDocument";

export default function ReportDetailPage() {
  const { reportId = "" } = useParams();
  const api = useApi();
  const [report, setReport] = useState<PublishedReportDetailV1 | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    setReport(null);
    api
      .getPublishedReport(reportId)
      .then((r) => {
        if (active) setReport(r);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, reportId, nonce]);

  if (error) {
    return (
      <div className="reading-column">
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      </div>
    );
  }
  if (!report) {
    return <Spin style={{ display: "block", margin: "96px auto" }} />;
  }

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "40px 24px 96px" }}>
      <Row gutter={48}>
        <Col flex="140px">
          <nav className="section-nav">
            {SECTION_ORDER.map(({ key, title }) => (
              <div key={key}>
                <a href={`#section-${key}`}>{title}</a>
              </div>
            ))}
          </nav>
        </Col>
        <Col flex="auto" style={{ maxWidth: 680 }}>
          <Typography.Title level={3} style={{ marginTop: 0 }}>
            {report.title}
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 32 }}>
            {formatDateTime(report.published_at)} 发布 · 第 {report.version_number} 版
          </Typography.Paragraph>
          <ReportDocument sections={report.sections} citations={report.citations} serif />
        </Col>
      </Row>
    </div>
  );
}
