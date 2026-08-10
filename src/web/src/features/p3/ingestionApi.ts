import { API, getSelectedEnterprise } from "../../api";
import { reasonCopy } from "./reasonCopy";
import type {
  DocumentCollection,
  DocumentDetail,
  IngestionCapabilities,
  IngestionErrorEnvelope,
  PageText,
  PreviewManifest,
  VersionSummary,
  WorksheetGrid,
} from "./types";

export const P3_INGESTION_BASE = "/v1/ingestion";

export class IngestionApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(reasonCopy(code));
    this.name = "IngestionApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

interface RequestOptions {
  token: string | null;
  method?: "GET" | "POST";
  body?: BodyInit;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function assertP3Path(path: string): void {
  if (!path.startsWith(P3_INGESTION_BASE)) {
    throw new IngestionApiError(0, "INVALID_INGESTION_PATH", false);
  }
}

function requestHeaders(options: RequestOptions): Headers {
  const headers = new Headers();
  if (options.token) headers.set("Authorization", "Bearer " + options.token);
  const enterpriseId = getSelectedEnterprise();
  if (enterpriseId) headers.set("X-Enterprise-Id", enterpriseId);
  if (options.idempotencyKey) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }
  return headers;
}

function normalizeErrorEnvelope(value: unknown): IngestionErrorEnvelope {
  if (!value || typeof value !== "object") return {};
  return value as IngestionErrorEnvelope;
}

async function responseError(response: Response): Promise<IngestionApiError> {
  let envelope: IngestionErrorEnvelope = {};
  try {
    envelope = normalizeErrorEnvelope(await response.json());
  } catch {
    envelope = {};
  }
  const detail = envelope.detail;
  const code =
    detail && typeof detail.code === "string" && /^[A-Z0-9_]{1,80}$/.test(detail.code)
      ? detail.code
      : response.status === 404
        ? "NOT_FOUND"
        : "HTTP_" + response.status;
  return new IngestionApiError(
    response.status,
    code,
    Boolean(detail && detail.retryable),
  );
}

async function request(path: string, options: RequestOptions): Promise<Response> {
  assertP3Path(path);
  if (!getSelectedEnterprise()) {
    throw new IngestionApiError(0, "TENANT_CONTEXT_REQUIRED", false);
  }
  let response: Response;
  try {
    response = await fetch(API + path, {
      method: options.method ?? "GET",
      headers: requestHeaders(options),
      body: options.body,
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw new IngestionApiError(0, "REQUEST_ABORTED", false);
    }
    throw new IngestionApiError(0, "NETWORK_ERROR", true);
  }
  if (!response.ok) throw await responseError(response);
  return response;
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  const response = await request(path, options);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new IngestionApiError(response.status, "INVALID_RESPONSE", true);
  }
}

export function userFacingIngestionError(error: unknown): string {
  if (error instanceof IngestionApiError) return reasonCopy(error.code);
  return reasonCopy("NETWORK_ERROR");
}

export function getIngestionCapabilities(
  token: string | null,
  signal?: AbortSignal,
): Promise<IngestionCapabilities> {
  return requestJson<IngestionCapabilities>(P3_INGESTION_BASE + "/capabilities", {
    token,
    signal,
  });
}

export interface ListDocumentParams {
  status?: string;
  contentType?: string;
  cursor?: string | null;
  limit?: number;
}

export function listIngestionDocuments(
  token: string | null,
  params: ListDocumentParams = {},
  signal?: AbortSignal,
): Promise<DocumentCollection> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.contentType) query.set("content_type", params.contentType);
  if (params.cursor) query.set("cursor", params.cursor);
  query.set("limit", String(params.limit ?? 20));
  return requestJson<DocumentCollection>(
    P3_INGESTION_BASE + "/documents?" + query.toString(),
    { token, signal },
  );
}

export function createIngestionDocument(
  token: string | null,
  displayName: string,
  file: File,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  const body = new FormData();
  body.set("display_name", displayName);
  body.set("file", file, file.name);
  return requestJson<DocumentDetail>(P3_INGESTION_BASE + "/documents", {
    token,
    method: "POST",
    body,
    idempotencyKey,
    signal,
  });
}

export function getIngestionDocument(
  token: string | null,
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  return requestJson<DocumentDetail>(
    P3_INGESTION_BASE + "/documents/" + encodeURIComponent(documentId),
    { token, signal },
  );
}

export function uploadDocumentVersion(
  token: string | null,
  documentId: string,
  file: File,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  const body = new FormData();
  body.set("file", file, file.name);
  return requestJson<VersionSummary>(
    P3_INGESTION_BASE +
      "/documents/" +
      encodeURIComponent(documentId) +
      "/versions",
    { token, method: "POST", body, idempotencyKey, signal },
  );
}

export function getIngestionVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return requestJson<VersionSummary>(
    P3_INGESTION_BASE + "/versions/" + encodeURIComponent(versionId),
    { token, signal },
  );
}

function versionAction(
  token: string | null,
  versionId: string,
  action: "process" | "retry" | "release" | "reject",
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return requestJson<VersionSummary>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/" +
      action,
    { token, method: "POST", signal },
  );
}

export function processIngestionVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return versionAction(token, versionId, "process", signal);
}

export function retryIngestionVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return versionAction(token, versionId, "retry", signal);
}

export function releaseIngestionVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return versionAction(token, versionId, "release", signal);
}

export function rejectIngestionVersion(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<VersionSummary> {
  return versionAction(token, versionId, "reject", signal);
}

export function getPreviewManifest(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<PreviewManifest> {
  return requestJson<PreviewManifest>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/preview",
    { token, signal },
  );
}

export async function getPreviewImageUnitBlob(
  token: string | null,
  versionId: string,
  unitId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const response = await request(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/preview/units/" +
      encodeURIComponent(unitId) +
      "/content",
    { token, signal },
  );
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.toLowerCase().split(";", 1)[0].trim() !== "image/jpeg") {
    throw new IngestionApiError(response.status, "UNSAFE_PREVIEW_RESPONSE", false);
  }
  return response.blob();
}

export async function getPageTextUnit(
  token: string | null,
  versionId: string,
  unitId: string,
  signal?: AbortSignal,
): Promise<PageText> {
  const payload = await requestJson<PageText>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/preview/units/" +
      encodeURIComponent(unitId) +
      "/content",
    { token, signal },
  );
  if (
    !Array.isArray(payload.lines) ||
    !payload.lines.every((line) => typeof line === "string") ||
    typeof payload.truncated !== "boolean"
  ) {
    throw new IngestionApiError(200, "INVALID_RESPONSE", false);
  }
  return payload;
}

export function getWorksheetGrid(
  token: string | null,
  versionId: string,
  unitId: string,
  rowOffset: number,
  rowLimit: number,
  signal?: AbortSignal,
): Promise<WorksheetGrid> {
  const query = new URLSearchParams({
    row_offset: String(Math.max(0, rowOffset)),
    row_limit: String(Math.max(1, rowLimit)),
  });
  return requestJson<WorksheetGrid>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/preview/units/" +
      encodeURIComponent(unitId) +
      "/grid?" +
      query.toString(),
    { token, signal },
  );
}
