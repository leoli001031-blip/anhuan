import { API, getSelectedEnterprise } from "../../api";
import { p4ReasonCopy } from "./reasonCopy";
import type {
  BusinessReportCollection,
  BusinessReport,
  BusinessReportDetail,
  CreateBusinessReportInput,
  CreateCrmAccountInput,
  CreateCrmContactInput,
  CreateCrmFollowUpInput,
  CreateReportVersionInput,
  CrmAccountCollection,
  CrmAccount,
  CrmAccountDetail,
  CrmContact,
  CrmFollowUp,
  DashboardOverview,
  P4ErrorEnvelope,
  ReportVersionDetail,
  ReportVersionSummary,
  UpdateCrmAccountInput,
  UpdateCrmContactInput,
} from "./types";

export const P4_VIEWS_REPORTS_BASE = "/v1/views-reports";

export class ViewsReportsApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(p4ReasonCopy(code));
    this.name = "ViewsReportsApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

interface RequestOptions {
  token: string | null;
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
}

function assertP4Path(path: string): void {
  if (!path.startsWith(P4_VIEWS_REPORTS_BASE)) {
    throw new ViewsReportsApiError(0, "INVALID_P4_PATH", false);
  }
}

function requestHeaders(options: RequestOptions): Headers {
  const headers = new Headers();
  if (options.token) headers.set("Authorization", "Bearer " + options.token);
  const enterpriseId = getSelectedEnterprise();
  if (enterpriseId) headers.set("X-Enterprise-Id", enterpriseId);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  return headers;
}

function normalizeErrorEnvelope(value: unknown): P4ErrorEnvelope {
  if (!value || typeof value !== "object") return {};
  return value as P4ErrorEnvelope;
}

async function responseError(response: Response): Promise<ViewsReportsApiError> {
  let envelope: P4ErrorEnvelope = {};
  try {
    envelope = normalizeErrorEnvelope(await response.json());
  } catch {
    envelope = {};
  }
  const detail = envelope.detail;
  const code =
    typeof detail === "string" && /^[A-Z0-9_]{1,80}$/.test(detail)
      ? detail
      : detail && typeof detail !== "string" && typeof detail.code === "string" && /^[A-Z0-9_]{1,80}$/.test(detail.code)
        ? detail.code
      : response.status === 404
        ? "NOT_FOUND"
        : "HTTP_" + response.status;
  return new ViewsReportsApiError(
    response.status,
    code,
    Boolean(detail && typeof detail !== "string" && detail.retryable),
  );
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  assertP4Path(path);
  if (!getSelectedEnterprise()) {
    throw new ViewsReportsApiError(0, "TENANT_CONTEXT_REQUIRED", false);
  }
  let response: Response;
  try {
    response = await fetch(API + path, {
      method: options.method ?? "GET",
      headers: requestHeaders(options),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (
      options.signal?.aborted ||
      (error instanceof DOMException && error.name === "AbortError")
    ) {
      throw new ViewsReportsApiError(0, "REQUEST_ABORTED", false);
    }
    throw new ViewsReportsApiError(0, "NETWORK_ERROR", true);
  }
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ViewsReportsApiError(response.status, "INVALID_RESPONSE", true);
  }
}

function itemPath(segment: string, id: string): string {
  return P4_VIEWS_REPORTS_BASE + segment + "/" + encodeURIComponent(id);
}

export function userFacingViewsReportsError(error: unknown): string {
  if (error instanceof ViewsReportsApiError) return p4ReasonCopy(error.code);
  return p4ReasonCopy("NETWORK_ERROR");
}

export function isViewsReportsRequestAborted(error: unknown): boolean {
  return error instanceof ViewsReportsApiError && error.code === "REQUEST_ABORTED";
}

export function getRoleDashboard(
  token: string | null,
  signal?: AbortSignal,
): Promise<DashboardOverview> {
  return requestJson<DashboardOverview>(P4_VIEWS_REPORTS_BASE + "/dashboard", {
    token,
    signal,
  });
}

export function listCrmAccounts(
  token: string | null,
  signal?: AbortSignal,
): Promise<CrmAccountCollection> {
  return requestJson<CrmAccountCollection>(P4_VIEWS_REPORTS_BASE + "/crm/accounts", {
    token,
    signal,
  });
}

export function createCrmAccount(
  token: string | null,
  input: CreateCrmAccountInput,
  signal?: AbortSignal,
): Promise<CrmAccount> {
  return requestJson<CrmAccount>(P4_VIEWS_REPORTS_BASE + "/crm/accounts", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function getCrmAccount(
  token: string | null,
  accountId: string,
  signal?: AbortSignal,
): Promise<CrmAccountDetail> {
  return requestJson<CrmAccountDetail>(itemPath("/crm/accounts", accountId), {
    token,
    signal,
  });
}

export function updateCrmAccount(
  token: string | null,
  accountId: string,
  input: UpdateCrmAccountInput,
  signal?: AbortSignal,
): Promise<CrmAccount> {
  return requestJson<CrmAccount>(itemPath("/crm/accounts", accountId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}

export function createCrmContact(
  token: string | null,
  accountId: string,
  input: CreateCrmContactInput,
  signal?: AbortSignal,
): Promise<CrmContact> {
  return requestJson<CrmContact>(itemPath("/crm/accounts", accountId) + "/contacts", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function updateCrmContact(
  token: string | null,
  contactId: string,
  input: UpdateCrmContactInput,
  signal?: AbortSignal,
): Promise<CrmContact> {
  return requestJson<CrmContact>(itemPath("/crm/contacts", contactId), {
    token,
    method: "PATCH",
    body: input,
    signal,
  });
}

export function createCrmFollowUp(
  token: string | null,
  accountId: string,
  input: CreateCrmFollowUpInput,
  signal?: AbortSignal,
): Promise<CrmFollowUp> {
  return requestJson<CrmFollowUp>(itemPath("/crm/accounts", accountId) + "/follow-ups", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function listBusinessReports(
  token: string | null,
  signal?: AbortSignal,
): Promise<BusinessReportCollection> {
  return requestJson<BusinessReportCollection>(P4_VIEWS_REPORTS_BASE + "/reports", {
    token,
    signal,
  });
}

export function createBusinessReport(
  token: string | null,
  input: CreateBusinessReportInput,
  signal?: AbortSignal,
): Promise<BusinessReport> {
  return requestJson<BusinessReport>(P4_VIEWS_REPORTS_BASE + "/reports", {
    token,
    method: "POST",
    body: input,
    signal,
  });
}

export function getBusinessReport(
  token: string | null,
  reportId: string,
  signal?: AbortSignal,
): Promise<BusinessReportDetail> {
  return requestJson<BusinessReportDetail>(itemPath("/reports", reportId), {
    token,
    signal,
  });
}

export function createReportVersion(
  token: string | null,
  reportId: string,
  input: CreateReportVersionInput,
  signal?: AbortSignal,
): Promise<ReportVersionSummary> {
  return requestJson<ReportVersionSummary>(itemPath("/reports", reportId) + "/versions", {
    token,
    method: "POST",
    body: {
      change_note: input.change_note ?? null,
      document_version_ids: input.document_version_ids ?? [],
    },
    signal,
  });
}

export function getReportVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<ReportVersionDetail> {
  return requestJson<ReportVersionDetail>(itemPath("/report-versions", versionId), {
    token,
    signal,
  });
}

export function archiveBusinessReport(
  token: string | null,
  reportId: string,
  signal?: AbortSignal,
): Promise<BusinessReport> {
  return requestJson<BusinessReport>(itemPath("/reports", reportId) + "/archive", {
    token,
    method: "POST",
    signal,
  });
}
