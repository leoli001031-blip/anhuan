import { useEffect, useState } from "react";
import { Button, Progress, Spin, Typography } from "antd";
import { Link } from "react-router-dom";
import { ENTERPRISE_CHANGED_EVENT, getTenantGeneration } from "../../api";
import { useApi } from "../../adapters";
import ErrorState from "../../components/ErrorState";
import {
  MANAGEMENT_HEALTH_BOUNDARY,
  healthToneColor,
  landHealthIfCurrent,
  type ManagementHealthSnapshot,
} from "../../features/managementHealth";

export default function HealthScorePage() {
  const api = useApi();
  const [snapshot, setSnapshot] = useState<ManagementHealthSnapshot | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const onChange = () => setEpoch((value) => value + 1);
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(ENTERPRISE_CHANGED_EVENT, onChange);
  }, []);

  useEffect(() => {
    let active = true;
    const born = getTenantGeneration();
    setLoading(true);
    setError(null);
    api
      .getLatestManagementHealth()
      .then((next) => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setSnapshot(next);
        });
      })
      .catch((caught) => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setError(caught);
          setSnapshot(null);
        });
      })
      .finally(() => {
        landHealthIfCurrent(born, getTenantGeneration(), () => {
          if (!active) return;
          setLoading(false);
        });
      });
    return () => {
      active = false;
    };
  }, [api, nonce, epoch]);

  if (loading) {
    return (
      <main className="portal-page health-page">
        <Spin style={{ display: "block", margin: "48px auto" }} />
      </main>
    );
  }

  if (error) {
    return (
      <main className="portal-page health-page">
        <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />
      </main>
    );
  }

  if (!snapshot) {
    return (
      <main className="portal-page health-page">
        <div className="portal-breadcrumb">
          <Link to="/portal">首页</Link><span>/</span><span>安环管理健康度</span>
        </div>
        <Typography.Title level={1} className="portal-page__title">
          安环管理健康度
        </Typography.Title>
        <Typography.Paragraph type="secondary" className="portal-page__subtitle">
          基于已发布材料与分析报告形成的管理参考
        </Typography.Paragraph>
        <section className="health-unavailable">
          <span className="health-environment-label">正式环境评分</span>
          <Typography.Title level={2}>暂不评分</Typography.Title>
          <Typography.Paragraph type="secondary">
            当前没有已发布的健康度快照。页面不会自行计算或回退到示例分数。
          </Typography.Paragraph>
          <Button type="primary">
            <Link to="/portal/reports">查看已发布报告</Link>
          </Button>
        </section>
        <section className="health-boundary-panel">
          <Typography.Title level={4}>使用说明</Typography.Title>
          <Typography.Paragraph>{MANAGEMENT_HEALTH_BOUNDARY}</Typography.Paragraph>
        </section>
      </main>
    );
  }

  const isDeterministic = snapshot.evidenceMode === "deterministic_local";

  return (
    <main className="portal-page health-page">
      <div className="portal-breadcrumb">
        <Link to="/portal">首页</Link><span>/</span><span>安环管理健康度</span>
      </div>
      <Typography.Title level={1} className="portal-page__title">
        安环管理健康度
      </Typography.Title>
      <Typography.Paragraph type="secondary" className="portal-page__subtitle">
        基于已发布材料与本次分析报告形成的管理参考
      </Typography.Paragraph>
      <div className={`health-environment-grid${isDeterministic ? "" : " health-environment-grid--single"}`}>
        <section className="health-detail-hero health-detail-hero--scored">
          <span className="health-environment-label">
            {isDeterministic ? "测试环境·确定性评分" : "正式环境评分"}
          </span>
          <div className="health-detail-hero__content">
            <div className="health-score-line health-score-line--detail">
              <strong>{snapshot.score}</strong>
              <span>/ {snapshot.maxScore}</span>
            </div>
            <div className="health-detail-hero__status">
              <strong>{snapshot.statusLabel}</strong>
              <span>评估日期&nbsp;&nbsp;{snapshot.assessedOn}</span>
              <span>{snapshot.basisLabel}</span>
            </div>
          </div>
          <Link to={`/portal/reports/${snapshot.reportId}`}>
            查看本期分析报告&nbsp;›
          </Link>
        </section>
        {isDeterministic ? (
          <section className="health-formal-pending" aria-labelledby="formal-health-heading">
            <span className="health-environment-label health-environment-label--formal">
              正式环境评分
            </span>
            <Typography.Title id="formal-health-heading" level={2}>暂不评分</Typography.Title>
            <Typography.Paragraph>
              当前没有可审计的正式环境健康度快照，页面不会把测试分数当作正式结论。
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              正式快照完成材料发布与校验后，才会在这里展示。
            </Typography.Text>
          </section>
        ) : null}
      </div>

      <div className="health-detail-grid">
        <section className="health-dimensions" aria-labelledby="dimension-heading">
          <Typography.Title id="dimension-heading" level={3}>
            维度明细
          </Typography.Title>
          {snapshot.dimensions.map((dimension) => (
            <div className="health-dimension" key={dimension.key}>
              <div className="health-dimension__copy">
                <strong>{dimension.label}</strong>
                <span>{dimension.summary}</span>
              </div>
              <Progress
                percent={(dimension.score / dimension.maxScore) * 100}
                showInfo={false}
                strokeColor={healthToneColor(dimension.tone)}
                railColor="var(--eco-progress-trail)"
                size="small"
              />
              <strong style={{ color: healthToneColor(dimension.tone) }}>
                {dimension.score}/{dimension.maxScore}
              </strong>
            </div>
          ))}
        </section>

        <aside className="health-detail-aside">
          <section className="health-priorities" aria-labelledby="priority-heading">
            <Typography.Title id="priority-heading" level={3}>
              优先改善事项
            </Typography.Title>
            {snapshot.priorities.map((priority, index) => (
              <div className="health-priority" key={priority.title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{priority.title}</strong>
                <em className={index === 0 ? "health-priority--high" : undefined}>
                  {priority.priorityLabel}
                </em>
              </div>
            ))}
            <Button className="health-report-button" block>
              <Link to={`/portal/reports/${snapshot.reportId}`}>查看对应分析报告</Link>
            </Button>
          </section>

          <section className="health-basis">
            <Typography.Title level={3}>评分依据</Typography.Title>
            <dl>
              <div><dt>资料范围</dt><dd>已发布企业材料</dd></div>
              <div><dt>关联报告</dt><dd>{snapshot.reportTitle}</dd></div>
              <div><dt>更新方式</dt><dd>报告发布后形成新快照</dd></div>
            </dl>
          </section>
        </aside>
      </div>

      <section className="health-boundary-panel">
        <Typography.Title level={3}>使用说明</Typography.Title>
        <Typography.Paragraph>
          安环管理健康度用于展示资料完整度、管理成熟度和改善优先级，不替代法定合规评价、执法结论、认证结论或生产放行。分值变化应以已发布材料与报告快照为准。
        </Typography.Paragraph>
      </section>
    </main>
  );
}
