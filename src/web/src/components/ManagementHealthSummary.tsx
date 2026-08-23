import { Progress, Typography } from "antd";
import { Link } from "react-router-dom";
import {
  MANAGEMENT_HEALTH_BOUNDARY,
  healthToneColor,
  type ManagementHealthSnapshot,
} from "../features/managementHealth";

export default function ManagementHealthSummary({
  snapshot,
}: {
  snapshot: ManagementHealthSnapshot | null;
}) {
  if (!snapshot) {
    return (
      <section className="health-summary health-summary--empty" aria-labelledby="health-heading">
        <div>
          <Typography.Title id="health-heading" level={4} className="health-summary__title">
            安环管理健康度
          </Typography.Title>
          <Typography.Title level={2} className="health-empty__status">
            暂不评分
          </Typography.Title>
          <Typography.Paragraph type="secondary" className="health-empty__copy">
            当前尚无已发布的健康度快照。评分合同与审计链完成后，这里将展示评估日期、分项依据和改善优先级。
          </Typography.Paragraph>
        </div>
        <div className="health-summary__action">
          <Link to="/portal/health">查看说明</Link>
        </div>
      </section>
    );
  }

  return (
    <section className="health-summary" aria-labelledby="health-heading">
      <div className="health-summary__score">
        <Typography.Title id="health-heading" level={4} className="health-summary__title">
          安环管理健康度
        </Typography.Title>
        <div className="health-score-line">
          <strong>{snapshot.score}</strong>
          <span>/ {snapshot.maxScore}</span>
        </div>
      </div>
      <div className="health-summary__status">
        <strong>{snapshot.statusLabel}</strong>
        <span>评估日期&nbsp;&nbsp;{snapshot.assessedOn}</span>
        <span>{snapshot.basisLabel}</span>
        {snapshot.evidenceMode === "deterministic_local" ? (
          <span>测试环境·确定性评分</span>
        ) : null}
      </div>
      <div className="health-summary__dimensions" aria-label="健康度维度摘要">
        {snapshot.dimensions.map((dimension) => (
          <div className="health-mini-dimension" key={dimension.key}>
            <span>{dimension.label}</span>
            <Progress
              percent={(dimension.score / dimension.maxScore) * 100}
              showInfo={false}
              strokeColor={healthToneColor(dimension.tone)}
              railColor="var(--eco-progress-trail)"
              size="small"
            />
            <strong>
              {dimension.score}/{dimension.maxScore}
            </strong>
          </div>
        ))}
      </div>
      <div className="health-summary__action">
        <Link to="/portal/health">查看健康度详情&nbsp;›</Link>
      </div>
      <Typography.Text type="secondary" className="health-summary__boundary">
        {MANAGEMENT_HEALTH_BOUNDARY}
      </Typography.Text>
    </section>
  );
}
