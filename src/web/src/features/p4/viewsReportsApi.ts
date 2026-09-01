import { tenantFetch, ApiError } from "../../api";
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

function mapTransportError(error: unknown): ViewsReportsApiError {
  if (error instanceof ViewsReportsApiError) return error;
  if (error instanceof ApiError) {
    if (error.code === "TENANT_SNAPSHOT_UNREADY") {
      return new ViewsReportsApiError(0, "TENANT_CONTEXT_REQUIRED", false);
    }
    return new ViewsReportsApiError(error.status, error.code, error.retryable);
  }
  return new ViewsReportsApiError(0, "NETWORK_ERROR", true);
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  assertP4Path(path);
  try {
    const result = await tenantFetch<T>(path, {
      method: options.method,
      token: options.token,
      body: options.body,
      signal: options.signal,
      parse: "json",
    });
    return result.payload;
  } catch (error) {
    throw mapTransportError(error);
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
