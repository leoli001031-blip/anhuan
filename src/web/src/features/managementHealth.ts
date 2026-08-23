// 客户侧「安环管理健康度」目前只有视觉合同，没有后端评分合同。
// 正式环境必须由后端返回已发布、不可变且可审计的快照；前端不得自行求和或补默认分。

export type ManagementHealthTone = "positive" | "attention" | "priority";

export type ManagementHealthDimensionKey =
  | "material-completeness"
  | "permits"
  | "monitoring"
  | "remediation"
  | "expiry"
  | "evidence";

export interface ManagementHealthDimension {
  key: ManagementHealthDimensionKey;
  label: string;
  score: number;
  maxScore: number;
  summary: string;
  tone: ManagementHealthTone;
}

export interface ManagementHealthPriority {
  title: string;
  priorityLabel: "高优先级" | "中优先级";
}

export interface ManagementHealthSnapshot {
  score: number;
  maxScore: 100;
  statusLabel: string;
  assessedOn: string;
  basisLabel: string;
  evidenceMode: "deterministic_local";
  reportId: string;
  versionId: string;
  versionNumber: number;
  reportTitle: string;
  dimensions: ManagementHealthDimension[];
  priorities: ManagementHealthPriority[];
}

export const MANAGEMENT_HEALTH_BOUNDARY =
  "该健康度用于资料管理与改善优先级参考，不替代法定合规评价、执法结论或生产放行。";

// 仅用于 VITE_MATERIAL_RAG_REPORT_MOCK=1 的本地视觉走查。
// 不得复用到 HTTP adapter，也不得在接口缺失时作为回退数据。
export const SYNTHETIC_MANAGEMENT_HEALTH: ManagementHealthSnapshot = {
  score: 60,
  maxScore: 100,
  statusLabel: "需重点改善",
  assessedOn: "2026-08-23",
  basisLabel: "基于已发布材料与本次分析报告",
  evidenceMode: "deterministic_local",
  reportId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  versionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
  versionNumber: 2,
  reportTitle: "2026 年第二季度安环管理分析报告",
  dimensions: [
    {
      key: "material-completeness",
      label: "资料完整性",
      score: 12,
      maxScore: 15,
      summary: "核心资料已覆盖，仍有少量待补充",
      tone: "positive",
    },
    {
      key: "permits",
      label: "证照与批复",
      score: 14,
      maxScore: 20,
      summary: "需核对部分证照的有效期与适用范围",
      tone: "attention",
    },
    {
      key: "monitoring",
      label: "监测与台账",
      score: 13,
      maxScore: 20,
      summary: "连续性资料仍需补齐",
      tone: "attention",
    },
    {
      key: "remediation",
      label: "整改闭环",
      score: 8,
      maxScore: 25,
      summary: "整改证明与闭环记录不足",
      tone: "priority",
    },
    {
      key: "expiry",
      label: "风险与到期",
      score: 6,
      maxScore: 10,
      summary: "近期到期事项需跟进",
      tone: "attention",
    },
    {
      key: "evidence",
      label: "证据可信度",
      score: 7,
      maxScore: 10,
      summary: "部分结论仍需更强佐证",
      tone: "attention",
    },
  ],
  priorities: [
    { title: "补齐整改闭环材料", priorityLabel: "高优先级" },
    { title: "更新连续监测与运行台账", priorityLabel: "中优先级" },
    { title: "核对证照有效期与适用范围", priorityLabel: "中优先级" },
  ],
};

export function healthToneColor(tone: ManagementHealthTone): string {
  if (tone === "positive") return "var(--eco-primary)";
  if (tone === "priority") return "var(--eco-danger)";
  return "var(--eco-warning)";
}

export interface ManagementHealthSnapshotV1 {
  report_id: string;
  version_id: string;
  version_number: number;
  report_title: string;
  score: number;
  max_score: 100;
  status_label: string;
  assessed_on: string;
  basis_label: string;
  evidence_mode: "deterministic_local";
  dimensions: Array<{
    key: ManagementHealthDimensionKey;
    label: string;
    score: number;
    max_score: number;
    summary: string;
    tone: ManagementHealthTone;
  }>;
  priorities: Array<{ title: string; level: "high" | "medium" }>;
  boundary: string;
}

export function toUiHealthSnapshot(raw: ManagementHealthSnapshotV1): ManagementHealthSnapshot {
  return {
    score: raw.score,
    maxScore: 100,
    statusLabel: raw.status_label,
    assessedOn: raw.assessed_on,
    basisLabel: raw.basis_label,
    evidenceMode: raw.evidence_mode,
    reportId: raw.report_id,
    versionId: raw.version_id,
    versionNumber: raw.version_number,
    reportTitle: raw.report_title,
    dimensions: raw.dimensions.map((dimension) => ({
      key: dimension.key,
      label: dimension.label,
      score: dimension.score,
      maxScore: dimension.max_score,
      summary: dimension.summary,
      tone: dimension.tone,
    })),
    priorities: raw.priorities.map((priority) => ({
      title: priority.title,
      priorityLabel: priority.level === "high" ? "高优先级" : "中优先级",
    })),
  };
}

export function landHealthIfCurrent(
  born: number,
  current: number,
  apply: () => void,
): boolean {
  if (born !== current) return false;
  apply();
  return true;
}
