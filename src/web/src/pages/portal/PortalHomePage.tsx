// 客户门户 · 首页（/portal）。在 client-safe 服务事项合同落地前，
// 只消费客户已发布报告，不把内部 P2 工作台视图伪装成客户待办。
import { useEffect, useState } from "react";
import { Button, Spin, Typography } from "antd";
import { Link } from "react-router-dom";
import { ENTERPRISE_CHANGED_EVENT, getTenantGeneration } from "../../api";
import { useApi } from "../../adapters";
import type { PublishedReportSummaryV1 } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import ManagementHealthSummary from "../../components/ManagementHealthSummary";
import { formatDateTime } from "../../components/ReportDocument";
import type { ManagementHealthSnapshot } from "../../features/managementHealth";
import { landHealthIfCurrent } from "../../features/managementHealth";

export default function PortalHomePage() {
  const api = useApi();
  const [reports, setReports] = useState<PublishedReportSummaryV1[] | null>(null);
  const [reportsError, setReportsError] = useState<unknown>(null);
  const [reportsNonce, setReportsNonce] = useState(0);
  const [health, setHealth] = useState<ManagementHealthSnapshot | null>(null);
  const [healthError, setHealthError] = useState<unknown>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthNonce, setHealthNonce] = useState(0);
  const [tenantEpoch, setTenantEpoch] = useState(0);

  useEffect(() => {
    const onChange = () => setTenantEpoch((value) => value + 1);
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(ENTERPRISE_CHANGED_EVENT, onChange);
  }, []);

  useEffect(() => {
    let active = true;
    const born = getTenantGeneration();
    setReports(null);
    setReportsError(null);
    api
      .listPublishedReports()
      .then((next) => {
        if (!active || born !== getTenantGeneration()) return;
        setReports(next);
      })
      .catch((error) => {
        if (!active || born !== getTenantGeneration()) return;
        setReportsError(error);
      });
    return () => {
      active = false;
    };
  }, [api, reportsNonce, tenantEpoch]);

  useEffect(() => {
    let active = true;
    const born = getTenantGeneration();
    setHealthLoading(true);
    setHealthError(null);
    api
      .getLatestManagementHealth()
      .then((snapshot) => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setHealth(snapshot);
        });
      })
      .catch((error) => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setHealthError(error);
          setHealth(null);
        });
      })
      .finally(() => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setHealthLoading(false);
        });
      });
    return () => {
      active = false;
    };
  }, [api, healthNonce, tenantEpoch]);

  const latestReport = reports?.[0];

  return (
    <main className="portal-page portal-home">
      <Typography.Title level={1} className="portal-page__title">
        企业安环服务总览
      </Typography.Title>
      <Typography.Paragraph className="portal-page__subtitle">
        查看最新分析结论、管理改善重点与服务安排。
      </Typography.Paragraph>

      <div className="portal-home__health">
        {healthLoading ? (
          <section className="health-summary health-summary--empty" aria-busy="true">
            <Spin style={{ display: "block", margin: "24px auto" }} />
          </section>
        ) : healthError ? (
          <section className="health-summary health-summary--empty">
            <ErrorState error={healthError} onRetry={() => setHealthNonce((n) => n + 1)} />
          </section>
        ) : (
          <ManagementHealthSummary snapshot={health} />
        )}
      </div>

      <section className="portal-section portal-section--latest" aria-labelledby="latest-report-heading">
        <Typography.Title id="latest-report-heading" level={3}>
          最新报告
        </Typography.Title>
        {reportsError ? (
          <ErrorState error={reportsError} onRetry={() => setReportsNonce((n) => n + 1)} />
        ) : reports === null ? (
          <Spin style={{ display: "block", margin: "24px auto" }} />
        ) : (
          <div className="portal-report-feature">
            {latestReport ? (
              <>
                <div className="portal-report-feature__body">
                  <Typography.Title level={4}>{latestReport.title}</Typography.Title>
                  <Typography.Text type="secondary">
                    已发布&nbsp;&nbsp;·&nbsp;&nbsp;{formatDateTime(latestReport.published_at)}&nbsp;&nbsp;·&nbsp;&nbsp;第 {latestReport.version_number} 版
                  </Typography.Text>
                  <Typography.Paragraph type="secondary">
                    报告基于企业已提供的资料，围绕现状、主要发现、风险与缺口及整改建议形成分析结论。
                  </Typography.Paragraph>
                </div>
                <Button type="primary">
                  <Link to={`/portal/reports/${latestReport.report_id}`}>查看报告&nbsp;›</Link>
                </Button>
              </>
            ) : (
              <div className="portal-report-feature__body">
                <Typography.Title level={4}>暂无已发布报告</Typography.Title>
                <Typography.Paragraph type="secondary">
                  报告发布后将会出现在这里。
                </Typography.Paragraph>
              </div>
            )}
          </div>
        )}
      </section>

      <div className="portal-support-grid">
        <section>
          <Typography.Title level={4}>服务安排</Typography.Title>
          <Typography.Text type="secondary">
            如有待办或上门安排，服务顾问将另行通知。
          </Typography.Text>
        </section>
        <section>
          <Typography.Title level={4}>资料状态说明</Typography.Title>
          <Typography.Text type="secondary">
            资料由服务商统一管理，最新结论以已发布的分析报告为准。建议定期完善资料，以提升分析结果的准确性与时效性。
          </Typography.Text>
        </section>
      </div>
    </main>
  );
}
