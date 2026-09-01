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
  ReportStatus,
  SectionKey,
  SectionV1,
  SessionAccessV1,
  VersionDetailV1,
  VersionHistoryItemV1,
} from "./types";
import {
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
  };
}
