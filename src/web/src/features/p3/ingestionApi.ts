import { tenantFetch, ApiError } from "../../api";
import { reasonCopy } from "./reasonCopy";
import type {
  AutoPipelineStatus,
  DocumentCollection,
  DocumentDetail,
  IngestionCapabilities,
  IngestionErrorEnvelope,
  KnowledgeScopeKind,
  KnowledgeScopeTarget,
  MaterialIntakeAnalysis,
  MaterialKind,
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
  method?: "GET" | "POST" | "PATCH";
  body?: BodyInit;
  contentType?: string;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function assertP3Path(path: string): void {
  if (!path.startsWith(P3_INGESTION_BASE)) {
    throw new IngestionApiError(0, "INVALID_INGESTION_PATH", false);
  }
}

function mapTransportError(error: unknown): IngestionApiError {
  if (error instanceof IngestionApiError) return error;
  if (error instanceof ApiError) {
    if (error.code === "TENANT_SNAPSHOT_UNREADY") {
      return new IngestionApiError(0, "TENANT_CONTEXT_REQUIRED", false);
    }
    return new IngestionApiError(error.status, error.code, error.retryable);
  }
  return new IngestionApiError(0, "NETWORK_ERROR", true);
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
  try {
    const result = await tenantFetch(path, {
      method: options.method,
      token: options.token,
      form: options.body instanceof FormData ? options.body : undefined,
      rawBody: typeof options.body === "string" ? options.body : undefined,
      contentType: options.contentType,
      extraHeaders: options.idempotencyKey
        ? { "Idempotency-Key": options.idempotencyKey }
        : undefined,
      signal: options.signal,
      parse: "response",
    });
    const response = result.response;
    if (!response) throw new IngestionApiError(0, "NETWORK_ERROR", true);
    if (!response.ok) throw await responseError(response);
    return response;
  } catch (error) {
    throw mapTransportError(error);
  }
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
  scopeKind?: KnowledgeScopeKind;
  clientAccountId?: string;
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
  if (params.scopeKind) query.set("scope_kind", params.scopeKind);
  if (params.clientAccountId) query.set("client_account_id", params.clientAccountId);
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
  declaredMaterialKind: MaterialKind = "unknown",
  knowledgeScope: KnowledgeScopeTarget = {
    kind: "service_provider",
    client_account_id: null,
  },
): Promise<DocumentDetail> {
  if (knowledgeScope.kind === "client" && !knowledgeScope.client_account_id) {
    throw new IngestionApiError(0, "CLIENT_ACCOUNT_REQUIRED", false);
  }
  const body = new FormData();
  body.set("display_name", displayName);
  body.set("declared_material_kind", declaredMaterialKind);
  body.set("knowledge_scope_kind", knowledgeScope.kind);
  if (knowledgeScope.kind === "client" && knowledgeScope.client_account_id) {
    body.set("client_account_id", knowledgeScope.client_account_id);
  }
  body.set("file", file, file.name);
  return requestJson<DocumentDetail>(P3_INGESTION_BASE + "/documents", {
    token,
    method: "POST",
    body,
    idempotencyKey,
    signal,
  });
}

export function setMaterialIntakeClassification(
  token: string | null,
  analysisId: string,
  materialKind: MaterialKind,
  signal?: AbortSignal,
): Promise<MaterialIntakeAnalysis> {
  return requestJson<MaterialIntakeAnalysis>(
    P3_INGESTION_BASE +
      "/material-analyses/" +
      encodeURIComponent(analysisId) +
      "/classification",
    {
      token,
      method: "PATCH",
      body: JSON.stringify({ material_kind: materialKind }),
      contentType: "application/json",
      signal,
    },
  );
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

export function getMaterialIntakeAnalysis(
  token: string | null,
  versionId: string,
  signal?: AbortSignal,
): Promise<MaterialIntakeAnalysis> {
  return requestJson<MaterialIntakeAnalysis>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/material-intake",
    { token, signal },
  );
}

export function getAutoPipelineStatus(
  token: string | null,
  versionId: string,
  clientAccountId?: string,
  signal?: AbortSignal,
): Promise<AutoPipelineStatus> {
  const query = new URLSearchParams();
  if (clientAccountId) query.set("client_account_id", clientAccountId);
  const queryString = query.toString();
  const suffix = queryString ? "?" + queryString : "";
  return requestJson<AutoPipelineStatus>(
    P3_INGESTION_BASE +
      "/versions/" +
      encodeURIComponent(versionId) +
      "/auto-pipeline" +
      suffix,
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
