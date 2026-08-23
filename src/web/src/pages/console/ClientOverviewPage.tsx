// 运营台 · 客户总览（/console/clients/:clientId）
// 只呈现已有 API 真实支持的字段：服务阶段、下次跟进、资料状态、最新报告。
// 开放/逾期事项与最近服务无客户维度 API——不造假，见 A_ECO_MVP_RECOVERY_BLOCKED.md。
import { useEffect, useState, type ReactNode } from "react";
import { Col, Row, Spin, Typography } from "antd";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../../adapters";
import type {
  MaterialItem,
  ProviderReportSummaryV1,
  ReportStatus,
} from "../../adapters/types";
import {
  CLIENT_STAGE_LABEL,
  MATERIAL_STATUS_LABEL,
  REPORT_STATUS_LABEL,
} from "../../adapters/types";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../components/ReportDocument";
import StatusDot, { type StatusTone } from "../../components/StatusDot";
import ClientShell from "./ClientShell";
import { useClient } from "./useClient";

const MATERIAL_TONE: Record<string, StatusTone> = {
  processing: "processing",
  ready: "success",
  blocked: "warning",
  failed: "danger",
};

const REPORT_TONE: Record<ReportStatus, StatusTone> = {
  empty: "neutral",
  queued: "processing",
  generating: "processing",
  draft: "neutral",
  review_pending: "warning",
  changes_requested: "danger",
  approved: "success",
  published: "success",
  superseded: "neutral",
  withdrawn: "neutral",
  failed: "danger",
};

function OverviewBlock({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section style={{ marginBottom: 24 }}>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {title}
      </Typography.Text>
      <div style={{ marginTop: 4, fontSize: 15 }}>{children}</div>
    </section>
  );
}

function OverviewBody({ clientId }: { clientId: string }) {
  const api = useApi();
  const { client } = useClient(clientId);
  const [materials, setMaterials] = useState<MaterialItem[] | null>(null);
  const [reports, setReports] = useState<ProviderReportSummaryV1[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setMaterials(null);
    setReports(null);
    setError(null);
    Promise.all([api.listClientMaterials(clientId), api.listClientReports(clientId)])
      .then(([m, r]) => {
        if (!active) return;
        setMaterials(m);
        setReports(r);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, clientId, nonce]);

  if (error) return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  if (!client || materials === null || reports === null) {
    return <Spin style={{ display: "block", margin: "48px auto" }} />;
  }

  const materialCounts = new Map<string, number>();
  for (const m of materials) {
    materialCounts.set(m.status, (materialCounts.get(m.status) ?? 0) + 1);
  }
  const latestReport = [...reports].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at),
  )[0];

  return (
    <Row gutter={[48, 16]}>
      <Col xs={24} md={12}>
        <OverviewBlock title="服务阶段">
          {CLIENT_STAGE_LABEL[client.stage] ?? client.stage}
        </OverviewBlock>
        <OverviewBlock title="下次跟进">
          {client.nextFollowUpAt ? (
            formatDateTime(client.nextFollowUpAt)
          ) : (
            <Typography.Text type="secondary">未安排</Typography.Text>
          )}
        </OverviewBlock>
        <OverviewBlock title={`资料（共 ${materials.length} 份）`}>
          {materials.length === 0 ? (
            <Typography.Text type="secondary">暂无材料</Typography.Text>
          ) : (
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 14 }}>
              {(Object.keys(MATERIAL_STATUS_LABEL) as Array<keyof typeof MATERIAL_STATUS_LABEL>)
                .filter((s) => materialCounts.get(s))
                .map((s) => (
                  <span key={s}>
                    <StatusDot tone={MATERIAL_TONE[s]} label={MATERIAL_STATUS_LABEL[s]} />{" "}
                    {materialCounts.get(s)}
                  </span>
                ))}
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <Link to={`/console/clients/${clientId}/materials`}>查看资料</Link>
          </div>
        </OverviewBlock>
      </Col>
      <Col xs={24} md={12}>
        <OverviewBlock title="最新报告">
          {latestReport ? (
            <div>
              <Link to={`/console/clients/${clientId}/reports/${latestReport.report_id}`}>
                {latestReport.title}
                {latestReport.version_number > 0
                  ? ` · 第 ${latestReport.version_number} 版`
                  : ""}
              </Link>
              <div style={{ marginTop: 4 }}>
                <StatusDot
                  tone={REPORT_TONE[latestReport.current_status]}
                  label={REPORT_STATUS_LABEL[latestReport.current_status]}
                />
                <Typography.Text type="secondary" style={{ fontSize: 13, marginLeft: 8 }}>
                  更新于 {formatDateTime(latestReport.updated_at)}
                </Typography.Text>
              </div>
            </div>
          ) : (
            <Typography.Text type="secondary">暂无报告</Typography.Text>
          )}
          <div style={{ marginTop: 8 }}>
            <Link to={`/console/clients/${clientId}/reports`}>查看报告</Link>
          </div>
        </OverviewBlock>
      </Col>
    </Row>
  );
}

export default function ClientOverviewPage() {
  const { clientId = "" } = useParams();
  return (
    <ClientShell clientId={clientId}>
      <OverviewBody clientId={clientId} />
    </ClientShell>
  );
}
