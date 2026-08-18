import { api, ApiError } from "../../api";
import { applyAskToUi } from "./journeyMachine";
import type {
  ClosedQueryId,
  MaterialCitation,
  MaterialQaAskResult,
  MaterialQaUiState,
} from "./types";

const UAT_PATH = "/v1/local-uat/material-qa";

export function localUatRuntimeEnabled(): boolean {
  return import.meta.env.VITE_MATERIAL_RAG_UAT_LOCAL === "1";
}

export async function askFixedQuery(
  token: string | null,
  input: {
    queryId: ClosedQueryId;
    requestId: string;
    clientAccountId?: string | null;
    previous: MaterialQaUiState;
  },
): Promise<MaterialQaUiState> {
  try {
    const payload = await api<MaterialQaAskResult>(UAT_PATH, {
      method: "POST",
      token,
      body: {
        query_id: input.queryId,
        request_id: input.requestId,
        ...(input.clientAccountId
          ? { client_account_id: input.clientAccountId }
          : {}),
      },
    });
    const status = payload.refusal_reason === "REQUEST_IN_PROGRESS" ? 202 : 200;
    return applyAskToUi(input.previous, status, payload);
  } catch (reason) {
    if (reason instanceof ApiError) {
      return applyAskToUi(input.previous, reason.status, { detail: reason.code });
    }
    return applyAskToUi(input.previous, 0, { detail: "NETWORK_ERROR" });
  }
}

export async function rebuildScope(
  token: string | null,
  clientAccountId?: string | null,
): Promise<{ residual_count: number }> {
  return api<{ residual_count: number }>(`${UAT_PATH}/rebuild`, {
    method: "POST",
    token,
    body: clientAccountId ? { client_account_id: clientAccountId } : {},
  });
}

export async function deleteScope(
  token: string | null,
  clientAccountId?: string | null,
): Promise<{ residual_count: number }> {
  return api<{ residual_count: number }>(`${UAT_PATH}/delete`, {
    method: "POST",
    token,
    body: clientAccountId ? { client_account_id: clientAccountId } : {},
  });
}

export async function openCitation(
  token: string | null,
  citation: Pick<MaterialCitation, "document_record_id" | "document_version_id">,
): Promise<{ document_record_id: string; document_version_id: string; page_number: number }> {
  return api(`${UAT_PATH}/citation`, {
    method: "POST",
    token,
    body: {
      document_record_id: citation.document_record_id,
      document_version_id: citation.document_version_id,
    },
  });
}
