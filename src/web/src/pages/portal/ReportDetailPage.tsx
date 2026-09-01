// 客户端报告详情：白纸黑字的阅读页，锚点目录带当前章节态。
// 窄屏（<768px）：目录转为顶部横向滚动条；未发布/已撤回/他企业 → 后端统一 404。
import { useEffect, useState } from "react";
import { Button, Col, Row, Spin, Typography, message } from "antd";
import { useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import { saveHtmlReportArtifact } from "../../adapters/AnalysisReportApi";
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
  const [currentSection, setCurrentSection] = useState<string>(SECTION_ORDER[0].key);
  const [downloading, setDownloading] = useState(false);

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

  // 当前章节定位：滚动经过的章节高亮到目录
  useEffect(() => {
    if (!report) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setCurrentSection(entry.target.id.replace("section-", ""));
          }
        }
      },
      { rootMargin: "-15% 0px -75% 0px" },
    );
    for (const { key } of SECTION_ORDER) {
      const el = document.getElementById(`section-${key}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [report]);

  const downloadHtml = async () => {
    setDownloading(true);
    try {
      const artifact = await api.getPublishedHtmlArtifact(reportId);
      saveHtmlReportArtifact(artifact);
      message.success("HTML 报告已开始下载");
    } catch {
      message.error("HTML 报告下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

  const downloadPdf = async () => {
    setDownloading(true);
    try {
      const artifact = await api.getPublishedPdfArtifact(reportId);
      saveHtmlReportArtifact(artifact);
      message.success("PDF 报告已开始下载");
    } catch {
      message.error("PDF 报告下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

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
    <main className="portal-report-reading">
      <Row gutter={40} wrap>
        <Col className="report-nav-col">
          <nav className="section-nav" aria-label="章节目录">
            {SECTION_ORDER.map(({ key, title }) => (
              <div key={key}>
                <a
                  href={`#section-${key}`}
                  className={currentSection === key ? "section-nav--current" : undefined}
                  aria-current={currentSection === key ? "true" : undefined}
                >
                  {title}
                </a>
              </div>
            ))}
          </nav>
        </Col>
        <Col className="report-body-col">
          <article className="report-paper">
            <div className="report-paper__header">
              <Typography.Title level={2}>{report.title}</Typography.Title>
              <div className="report-paper__meta">
                <span className="portal-report-row__status">已发布</span>
                <Typography.Text type="secondary">
                  {formatDateTime(report.published_at)} · 第 {report.version_number} 版
                </Typography.Text>
                <Button
                  size="small"
                  data-report-action="download-pdf"
                  loading={downloading}
                  onClick={() => void downloadPdf()}
                  style={{ marginRight: 8 }}
                >
                  下载 PDF 报告
                </Button>
                <Button
                  size="small"
                  data-report-action="download-html"
                  loading={downloading}
                  onClick={() => void downloadHtml()}
                >
                  下载 HTML 报告
                </Button>
              </div>
            </div>
            <ReportDocument sections={report.sections} citations={report.citations} serif />
          </article>
        </Col>
      </Row>
    </main>
  );
}
