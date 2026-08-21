import type { MaterialQaPhase, MaterialQaUiState } from "./types";

export const PHASE_COPY: Record<MaterialQaPhase, string> = {
  disabled: "本地固定问答未启用。真实检索因密钥轮换保持阻断，页面不可提交自由文本。",
  loading: "正在按当前范围检索…",
  empty: "当前范围没有可引用的材料。",
  ready: "已返回当前范围的引用。点击引用可跳转到对应文档版本。",
  "in-progress": "同一请求仍在处理，请稍后重试。先前结果未作为成功覆盖。",
  conflict: "同一请求标识已绑定其他客户或场景，不能覆盖。",
  unavailable: "检索暂时不可用。先前答案与引用已清空。",
  denied: "记录不存在或当前范围无权访问。",
  retry: "可以重新发起固定场景检索。",
  recovery: "失败后已恢复空结果，可重新选择场景。",
};

export const REASON_COPY: Record<string, string> = {
  MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED: "自由提问未授权，问题不会外发。",
  QUERY_ID_NOT_AUTHORIZED: "该固定场景不在本地闭集中。",
  MATERIAL_CONTEXT_NOT_FOUND: "记录不存在或当前范围无权访问。",
  MATERIAL_CITATION_NOT_FOUND: "引用不存在或当前范围无权访问。",
  REQUEST_ID_CONFLICT: "同一请求标识已绑定其他客户或场景。",
  REQUEST_IN_PROGRESS: "同一请求仍在处理。",
  MATERIAL_RAG_UNAVAILABLE: "检索暂时不可用。",
  MATERIAL_RAG_UAT_LOCAL_DISABLED: "本地固定问答未启用。",
  NO_HITS: "当前范围没有可引用的材料。",
  LIVE_RETRIEVAL_BLOCKED_BY_KEY_ROTATION: "真实检索因密钥轮换保持阻断。",
};

export function reasonCopy(code: string | null | undefined): string {
  if (!code) return "处理未完成";
  return REASON_COPY[code] ?? `处理未完成（${code}）`;
}

const EMPTY: Pick<MaterialQaUiState, "answer" | "citations"> = {
  answer: null,
  citations: [],
};

export function applyAskToUi(
  previous: MaterialQaUiState,
  status: number,
  body: {
    answer?: string | null;
    citations?: MaterialQaUiState["citations"];
    refusal_reason?: string | null;
    detail?: string;
    scope_label?: string;
  },
): MaterialQaUiState {
  const scopeLabel = body.scope_label ?? previous.scopeLabel;
  if (status === 202) {
    return {
      ...previous,
      phase: "in-progress",
      code: "REQUEST_IN_PROGRESS",
      scopeLabel,
    };
  }
  if (status === 409) {
    return {
      phase: "conflict",
      ...EMPTY,
      code: "REQUEST_ID_CONFLICT",
      scopeLabel,
    };
  }
  if (status === 404) {
    return {
      phase: "denied",
      ...EMPTY,
      code: typeof body.detail === "string" ? body.detail : "MATERIAL_CONTEXT_NOT_FOUND",
      scopeLabel,
    };
  }
  if (status === 503) {
    return {
      phase: "unavailable",
      ...EMPTY,
      code: "MATERIAL_RAG_UNAVAILABLE",
      scopeLabel,
    };
  }
  if (status === 200) {
    const citations = body.citations ?? [];
    if (citations.length === 0) {
      return {
        phase: "empty",
        ...EMPTY,
        code: body.refusal_reason ?? "NO_HITS",
        scopeLabel,
      };
    }
    return {
      phase: "ready",
      answer: body.answer ?? null,
      citations,
      code: null,
      scopeLabel,
    };
  }
  return {
    phase: "retry",
    ...EMPTY,
    code: typeof body.detail === "string" ? body.detail : "RETRY",
    scopeLabel,
  };
}

export function recoveryState(scopeLabel: string): MaterialQaUiState {
  return {
    phase: "recovery",
    ...EMPTY,
    code: null,
    scopeLabel,
  };
}
