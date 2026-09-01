// Frozen-contract wire parsers. Missing required fields fail closed;
// callers must not invent defaults.
import { ApiError } from "./errors";
import type {
  Capability,
  CitationV1,
  GenerationAcceptedV1,
  JobStatusV1,
  ProductRole,
  ProviderReportSummaryV1,
  PublishedReportDetailV1,
  PublishedReportSummaryV1,
  ReviewChecklistV1,
  ReviewEventV1,
  ReportStatus,
  SectionKey,
  SectionV1,
  SessionAccessV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";
import type { ManagementHealthSnapshotV1 } from "../features/managementHealth";
import {
  REVIEW_CHECKLIST_KEYS,
  SECTION_ORDER,
  SESSION_SCHEMA,
  TEMPLATE_TITLE,
} from "./types";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SECTION_KEYS = new Set<SectionKey>(SECTION_ORDER.map((item) => item.key));
const PRODUCT_ROLES = new Set<ProductRole>(["provider_admin", "client_user"]);
const REPORT_STATUSES = new Set<ReportStatus>([
  "empty",
  "queued",
  "generating",
  "draft",
  "review_pending",
  "changes_requested",
  "approved",
  "published",
  "superseded",
  "withdrawn",
  "failed",
]);
const JOB_STATUSES = new Set(["queued", "generating", "draft", "failed"]);
const CAPABILITIES = new Set<Capability>([
  "list_client_reports",
  "create_report",
  "generate",
  "review",
  "publish",
  "withdraw",
  "list_published",
  "read_published",
]);
const ERROR_REASON_RE = /^[A-Z0-9_]{1,80}$/;
const REVIEW_ACTIONS = new Set<ReviewEventV1["action"]>([
  "submit",
  "return",
  "approve",
]);
const REVIEW_CHECKLIST_KEY_SET = new Set<string>(REVIEW_CHECKLIST_KEYS);

function wireError(code: string): never {
  throw new ApiError(0, code, false);
}

function asRecord(value: unknown, code: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    wireError(code);
  }
  return value as Record<string, unknown>;
}

function reqString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value.length === 0) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  return value;
}

function reqUuid(row: Record<string, unknown>, key: string): string {
  const value = reqString(row, key);
  if (!UUID_RE.test(value)) wireError("CONTRACT_FIELD_MISSING");
  return value;
}

function optUuid(row: Record<string, unknown>, key: string): string | null {
  const value = row[key];
  if (value === null) return null;
  if (typeof value !== "string" || !UUID_RE.test(value)) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  return value;
}

function reqInt(row: Record<string, unknown>, key: string, min: number): number {
  const value = row[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  return value;
}

function reqConst(row: Record<string, unknown>, key: string, expected: string): string {
  const value = reqString(row, key);
  if (value !== expected) wireError("CONTRACT_FIELD_MISSING");
  return value;
}

function reqArray(row: Record<string, unknown>, key: string): unknown[] {
  const value = row[key];
  if (!Array.isArray(value)) wireError("CONTRACT_FIELD_MISSING");
  return value;
}

export function parseSection(raw: unknown): SectionV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  const key = reqString(row, "key");
  if (!SECTION_KEYS.has(key as SectionKey)) wireError("CONTRACT_FIELD_MISSING");
  return {
    key: key as SectionKey,
    title: reqString(row, "title"),
    body: reqString(row, "body"),
  };
}

export function parseCitation(raw: unknown): CitationV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  return {
    citation_id: reqUuid(row, "citation_id"),
    document_version_id: reqUuid(row, "document_version_id"),
    documentName: reqString(row, "document_name"),
    versionNumber: reqInt(row, "version_number", 1),
    pageNumber: reqInt(row, "page_number", 1),
    excerpt: reqString(row, "excerpt"),
  };
}

export function parseSessionAccess(raw: unknown): SessionAccessV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", SESSION_SCHEMA);
  const productRole = reqString(row, "product_role");
  if (!PRODUCT_ROLES.has(productRole as ProductRole)) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  const capabilities = reqArray(row, "capabilities").map((item) => {
    if (typeof item !== "string" || !CAPABILITIES.has(item as Capability)) {
      wireError("CONTRACT_FIELD_MISSING");
    }
    return item as Capability;
  });
  return {
    schema: SESSION_SCHEMA,
    product_role: productRole as ProductRole,
    enterprise_id: reqUuid(row, "enterprise_id"),
    template_id: reqConst(row, "template_id", "enterprise-ehs-material-analysis-v1"),
    template_title: reqConst(row, "template_title", TEMPLATE_TITLE),
    capabilities,
  };
}

export function parsePublishedSummary(raw: unknown): PublishedReportSummaryV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  if (row.artifact_ready !== true) wireError("CONTRACT_FIELD_MISSING");
  return {
    report_id: reqUuid(row, "report_id"),
    version_id: reqUuid(row, "version_id"),
    version_number: reqInt(row, "version_number", 1),
    title: reqConst(row, "title", TEMPLATE_TITLE),
    published_at: reqString(row, "published_at"),
    artifact_ready: true,
  };
}

export function parsePublishedList(raw: unknown): PublishedReportSummaryV1[] {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-published-list-v1");
  return reqArray(row, "reports").map(parsePublishedSummary);
}

export function parsePublishedDetail(raw: unknown): PublishedReportDetailV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-published-detail-v1");
  if (row.artifact_ready !== true) wireError("CONTRACT_FIELD_MISSING");
  const sections = reqArray(row, "sections").map(parseSection);
  const citations = reqArray(row, "citations").map(parseCitation);
  if (sections.length !== 7 || citations.length < 1) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  return {
    schema: "anhuan-analysis-report-published-detail-v1",
    report_id: reqUuid(row, "report_id"),
    version_id: reqUuid(row, "version_id"),
    version_number: reqInt(row, "version_number", 1),
    title: reqConst(row, "title", TEMPLATE_TITLE),
    published_at: reqString(row, "published_at"),
    artifact_ready: true,
    sections,
    citations,
  };
}

function parseStatus(value: string): ReportStatus {
  if (!REPORT_STATUSES.has(value as ReportStatus)) wireError("CONTRACT_FIELD_MISSING");
  return value as ReportStatus;
}

export function parseProviderSummary(raw: unknown): ProviderReportSummaryV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  return {
    report_id: reqUuid(row, "report_id"),
    current_version_id: optUuid(row, "current_version_id"),
    current_status: parseStatus(reqString(row, "current_status")),
    version_number: reqInt(row, "version_number", 0),
    title: reqConst(row, "title", TEMPLATE_TITLE),
    updated_at: reqString(row, "updated_at"),
  };
}

export function parseProviderList(raw: unknown): ProviderReportSummaryV1[] {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-provider-list-v1");
  return reqArray(row, "reports").map(parseProviderSummary);
}

export function parseGeneration(raw: unknown): GenerationAcceptedV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-generation-v1");
  const status = reqString(row, "status");
  if (!JOB_STATUSES.has(status)) wireError("CONTRACT_FIELD_MISSING");
  return {
    schema: "anhuan-analysis-report-generation-v1",
    job_id: reqUuid(row, "job_id"),
    version_id: reqUuid(row, "version_id"),
    status: status as GenerationAcceptedV1["status"],
  };
}

export function parseJobStatus(raw: unknown): JobStatusV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-job-v1");
  const status = reqString(row, "status");
  if (!JOB_STATUSES.has(status)) wireError("CONTRACT_FIELD_MISSING");
  const errorReason = row.error_reason;
  if (
    errorReason !== null &&
    (typeof errorReason !== "string" || !ERROR_REASON_RE.test(errorReason))
  ) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  return {
    schema: "anhuan-analysis-report-job-v1",
    job_id: reqUuid(row, "job_id"),
    version_id: reqUuid(row, "version_id"),
    status: status as JobStatusV1["status"],
    error_reason: errorReason as string | null,
  };
}

export function parseVersionHistory(raw: unknown): VersionHistoryItemV1[] {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-version-history-v1");
  return reqArray(row, "versions").map((item) => {
    const version = asRecord(item, "CONTRACT_FIELD_MISSING");
    return {
      version_id: reqUuid(version, "version_id"),
      version_number: reqInt(version, "version_number", 1),
      status: parseStatus(reqString(version, "status")),
      created_at: reqString(version, "created_at"),
    };
  });
}

function parseReviewEvent(raw: unknown): ReviewEventV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  const action = reqString(row, "action");
  if (!REVIEW_ACTIONS.has(action as ReviewEventV1["action"])) {
    wireError("CONTRACT_FIELD_MISSING");
  }

  const checklistRow = asRecord(row.checklist, "CONTRACT_FIELD_MISSING");
  if (
    Object.entries(checklistRow).some(
      ([key, value]) =>
        !REVIEW_CHECKLIST_KEY_SET.has(key) || typeof value !== "boolean",
    )
  ) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  const checklist: Partial<ReviewChecklistV1> = {
    ...(typeof checklistRow.citation_traceable === "boolean"
      ? { citation_traceable: checklistRow.citation_traceable }
      : {}),
    ...(typeof checklistRow.risks_complete === "boolean"
      ? { risks_complete: checklistRow.risks_complete }
      : {}),
    ...(typeof checklistRow.usage_boundary === "boolean"
      ? { usage_boundary: checklistRow.usage_boundary }
      : {}),
  };

  const commentRaw = row.comment;
  if (
    commentRaw !== null &&
    (typeof commentRaw !== "string" ||
      commentRaw.trim().length === 0 ||
      commentRaw.length > 2_000)
  ) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  const comment = commentRaw as string | null;
  const checklistKeys = Object.keys(checklist);
  if (
    (action === "submit" && (checklistKeys.length !== 0 || comment !== null)) ||
    (action === "return" && comment === null) ||
    (action === "approve" &&
      (checklistKeys.length !== REVIEW_CHECKLIST_KEYS.length ||
        !REVIEW_CHECKLIST_KEYS.every((key) => checklist[key] === true)))
  ) {
    wireError("CONTRACT_FIELD_MISSING");
  }

  return {
    event_id: reqUuid(row, "event_id"),
    action: action as ReviewEventV1["action"],
    checklist,
    comment,
    created_at: reqString(row, "created_at"),
  };
}

export function parseVersionDetail(raw: unknown): VersionDetailV1 {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  reqConst(row, "schema", "anhuan-analysis-report-draft-v1");
  return {
    schema: "anhuan-analysis-report-draft-v1",
    report_id: reqUuid(row, "report_id"),
    version_id: reqUuid(row, "version_id"),
    version_number: reqInt(row, "version_number", 1),
    status: parseStatus(reqString(row, "status")),
    title: reqConst(row, "title", TEMPLATE_TITLE),
    sections: reqArray(row, "sections").map(parseSection),
    citations: reqArray(row, "citations").map(parseCitation),
    review_events: reqArray(row, "review_events").map(parseReviewEvent),
  };
}

const HEALTH_SCHEMA = "anhuan-analysis-report-health-v1";
const HEALTH_ENVELOPE_KEYS = ["schema", "snapshot"] as const;
const HEALTH_SNAPSHOT_KEYS = [
  "report_id",
  "version_id",
  "version_number",
  "report_title",
  "score",
  "max_score",
  "status_label",
  "assessed_on",
  "basis_label",
  "evidence_mode",
  "dimensions",
  "priorities",
  "boundary",
] as const;
const HEALTH_DIMENSION_KEYS = ["key", "label", "score", "max_score", "summary", "tone"] as const;
const HEALTH_PRIORITY_KEYS = ["title", "level"] as const;
const HEALTH_DIMENSION_SPECS = [
  { key: "material-completeness", max: 15 },
  { key: "permits", max: 20 },
  { key: "monitoring", max: 20 },
  { key: "remediation", max: 25 },
  { key: "expiry", max: 10 },
  { key: "evidence", max: 10 },
] as const;
const HEALTH_TONES = new Set(["positive", "attention", "priority"]);
const HEALTH_LEVELS = new Set(["high", "medium"]);
const HEALTH_ISO =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const HEALTH_LEAK = /provider|client|binding|scope|dataset|chunk|sha|lease|request[_-]?id/i;

function isRealUtcCalendarDate(value: string): boolean {
  if (!HEALTH_ISO.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  const utc = new Date(Date.UTC(year, month - 1, day));
  return (
    utc.getUTCFullYear() === year &&
    utc.getUTCMonth() === month - 1 &&
    utc.getUTCDate() === day
  );
}

function exactKeys(row: Record<string, unknown>, keys: readonly string[]): void {
  const actual = Object.keys(row);
  if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) {
    wireError("CONTRACT_FIELD_MISSING");
  }
}

function rejectLeaks(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(rejectLeaks);
    return;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
      if (HEALTH_LEAK.test(key)) wireError("CONTRACT_FIELD_MISSING");
      rejectLeaks(inner);
    }
  }
}

export function parseHealthEnvelope(
  raw: unknown,
): { schema: typeof HEALTH_SCHEMA; snapshot: ManagementHealthSnapshotV1 | null } {
  const row = asRecord(raw, "CONTRACT_FIELD_MISSING");
  exactKeys(row, HEALTH_ENVELOPE_KEYS);
  rejectLeaks(row);
  reqConst(row, "schema", HEALTH_SCHEMA);
  if (row.snapshot === null) {
    return { schema: HEALTH_SCHEMA, snapshot: null };
  }
  const snapshot = asRecord(row.snapshot, "CONTRACT_FIELD_MISSING");
  exactKeys(snapshot, HEALTH_SNAPSHOT_KEYS);
  if (reqInt(snapshot, "max_score", 100) !== 100) wireError("CONTRACT_FIELD_MISSING");
  const assessedOn = reqString(snapshot, "assessed_on");
  if (!isRealUtcCalendarDate(assessedOn)) wireError("CONTRACT_FIELD_MISSING");
  const evidenceMode = reqString(snapshot, "evidence_mode");
  if (evidenceMode !== "evidence_local") wireError("CONTRACT_FIELD_MISSING");
  const dimensionsRaw = reqArray(snapshot, "dimensions");
  if (dimensionsRaw.length !== HEALTH_DIMENSION_SPECS.length) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  const dimensions = dimensionsRaw.map((item, index) => {
    const dim = asRecord(item, "CONTRACT_FIELD_MISSING");
    exactKeys(dim, HEALTH_DIMENSION_KEYS);
    const spec = HEALTH_DIMENSION_SPECS[index];
    if (reqString(dim, "key") !== spec.key) wireError("CONTRACT_FIELD_MISSING");
    const maxScore = reqInt(dim, "max_score", spec.max);
    if (maxScore !== spec.max) wireError("CONTRACT_FIELD_MISSING");
    const score = reqInt(dim, "score", 0);
    if (score > spec.max) wireError("CONTRACT_FIELD_MISSING");
    const tone = reqString(dim, "tone");
    if (!HEALTH_TONES.has(tone)) wireError("CONTRACT_FIELD_MISSING");
    return {
      key: spec.key,
      label: reqString(dim, "label"),
      score,
      max_score: spec.max,
      summary: reqString(dim, "summary"),
      tone: tone as "positive" | "attention" | "priority",
    };
  });
  const score = reqInt(snapshot, "score", 0);
  const summed = dimensions.reduce((total, dim) => total + dim.score, 0);
  if (score !== summed || score > 100) wireError("CONTRACT_FIELD_MISSING");
  const prioritiesRaw = reqArray(snapshot, "priorities");
  if (prioritiesRaw.length < 1 || prioritiesRaw.length > 3) {
    wireError("CONTRACT_FIELD_MISSING");
  }
  const priorities = prioritiesRaw.map((item) => {
    const priority = asRecord(item, "CONTRACT_FIELD_MISSING");
    exactKeys(priority, HEALTH_PRIORITY_KEYS);
    const level = reqString(priority, "level");
    if (!HEALTH_LEVELS.has(level)) wireError("CONTRACT_FIELD_MISSING");
    return { title: reqString(priority, "title"), level: level as "high" | "medium" };
  });
  return {
    schema: HEALTH_SCHEMA,
    snapshot: {
      report_id: reqUuid(snapshot, "report_id"),
      version_id: reqUuid(snapshot, "version_id"),
      version_number: reqInt(snapshot, "version_number", 1),
      report_title: reqString(snapshot, "report_title"),
      score,
      max_score: 100,
      status_label: reqString(snapshot, "status_label"),
      assessed_on: assessedOn,
      basis_label: reqString(snapshot, "basis_label"),
      evidence_mode: "evidence_local",
      dimensions,
      priorities,
      boundary: reqString(snapshot, "boundary"),
    },
  };
}

export function managementHealthFromHttp(
  status: number,
  payload: unknown,
): { schema: typeof HEALTH_SCHEMA; snapshot: ManagementHealthSnapshotV1 | null } {
  if (status === 503) {
    throw new ApiError(503, "HEALTH_SNAPSHOT_UNAVAILABLE", true);
  }
  return parseHealthEnvelope(payload);
}
