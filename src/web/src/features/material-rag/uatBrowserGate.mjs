#!/usr/bin/env node
/**
 * Offline browser-equivalent machine gate for material-RAG UAT.
 * Synthetic catalog only. No network, files, or free text.
 */
const PHYSICAL = ["ds_must_not_leak", "chunk_must_not_leak", "scope_must_not_leak"];

const ENTERPRISE_A = "41000000-0000-4000-8000-000000000001";
const CLIENT_A = "41000000-0000-4000-8000-0000000000aa";
const CLIENT_B = "41000000-0000-4000-8000-0000000000bb";
const PROVIDER_DOC = "41000000-0000-4000-8000-000000000011";
const CLIENT_A_DOC = "41000000-0000-4000-8000-000000000021";
const ENTERPRISE_B_DOC = "41000000-0000-4000-8000-000000000091";

const PHASE_COPY = {
  disabled: "本地固定问答未启用。",
  loading: "正在按当前范围检索…",
  empty: "当前范围没有可引用的材料。",
  ready: "已返回当前范围的引用。",
  "in-progress": "同一请求仍在处理。",
  conflict: "同一请求标识已绑定其他客户或场景。",
  unavailable: "检索暂时不可用。先前答案与引用已清空。",
  denied: "记录不存在或当前范围无权访问。",
  retry: "可以重新发起固定场景检索。",
  recovery: "失败后已恢复空结果。",
};

function applyAskToUi(previous, status, body) {
  if (status === 202) {
    return { ...previous, phase: "in-progress", code: "REQUEST_IN_PROGRESS" };
  }
  if (status >= 400) {
    const phase =
      status === 409 ? "conflict" : status === 503 ? "unavailable" : status === 404 ? "denied" : "retry";
    return { phase, answer: null, citations: [], code: body.detail || body.refusal_reason || "ERROR" };
  }
  if (!body.citations || body.citations.length === 0) {
    return { phase: "empty", answer: null, citations: [], code: body.refusal_reason || "NO_HITS" };
  }
  return { phase: "ready", answer: body.answer, citations: body.citations, code: null };
}

function assertNoLeak(value) {
  const text = JSON.stringify(value);
  for (const token of PHYSICAL) {
    if (text.includes(token)) {
      throw new Error(`physical_id_token:${token}`);
    }
  }
  if (/"dataset_id"|"chunk_id"|"knowledge_scope_id"|"scope_ids"/.test(text)) {
    throw new Error("physical_id_key");
  }
}

function catalog(queryId, clientId) {
  const provider = {
    document_record_id: PROVIDER_DOC,
    scope_kind: "service_provider",
    snippet: "SYNTH_PROVIDER_HIT",
  };
  const clientA = {
    document_record_id: CLIENT_A_DOC,
    scope_kind: "client",
    snippet: "SYNTH_CLIENT_A_HIT",
  };
  if (queryId === "provider.shared") return [provider];
  if (queryId === "client.current") return clientId === CLIENT_A ? [clientA] : [];
  if (queryId === "combo.provider_client") {
    return clientId === CLIENT_A ? [provider, clientA] : [provider];
  }
  return [];
}

function main() {
  const asks = new Map();
  let deletedClientA = false;
  const phasesSeen = new Set(["disabled", "loading", "retry", "recovery"]);

  function ask(queryId, requestId, clientId) {
    const key = requestId;
    if (queryId === "cross.denied") {
      return { status: 404, body: { detail: "MATERIAL_CONTEXT_NOT_FOUND" } };
    }
    if (queryId === "fail.clear") {
      return { status: 503, body: { detail: "MATERIAL_RAG_UNAVAILABLE" } };
    }
    if (queryId === "progress.wait") {
      return { status: 202, body: { answer: null, citations: [], refusal_reason: "REQUEST_IN_PROGRESS" } };
    }
    const prior = asks.get(key);
    if (prior && (prior.queryId !== queryId || prior.clientId !== clientId)) {
      return { status: 409, body: { detail: "REQUEST_ID_CONFLICT" } };
    }
    if (prior) return prior.response;
    let citations = catalog(queryId, clientId);
    if (deletedClientA) {
      citations = citations.filter((item) => item.document_record_id !== CLIENT_A_DOC);
    }
    const response = {
      status: 200,
      body: {
        answer: citations.length ? "SYNTH_ANSWER" : null,
        citations,
        refusal_reason: citations.length ? null : "NO_HITS",
      },
    };
    asks.set(key, { queryId, clientId, response });
    return response;
  }

  let ui = { phase: "empty", answer: null, citations: [], code: null };

  const j1 = ask("provider.shared", "req-1", null);
  ui = applyAskToUi({ ...ui, phase: "loading" }, j1.status, j1.body);
  phasesSeen.add(ui.phase);
  if (ui.phase !== "ready" || ui.citations[0].document_record_id !== PROVIDER_DOC) {
    throw new Error("J1");
  }

  const j2 = ask("client.current", "req-2", CLIENT_A);
  ui = applyAskToUi(ui, j2.status, j2.body);
  if (ui.citations.some((item) => item.scope_kind !== "client")) throw new Error("J2");

  const comboA = ask("combo.provider_client", "req-3", CLIENT_A);
  const comboB = ask("combo.provider_client", "req-4", CLIENT_B);
  const idsA = comboA.body.citations.map((item) => item.document_record_id).sort();
  const idsB = comboB.body.citations.map((item) => item.document_record_id);
  if (idsA.join() !== [CLIENT_A_DOC, PROVIDER_DOC].sort().join()) throw new Error("J3A");
  if (idsB.join() !== PROVIDER_DOC || idsB.includes(CLIENT_A_DOC)) throw new Error("J3B");
  ui = applyAskToUi(ui, comboB.status, comboB.body);
  phasesSeen.add(ui.phase);

  const denied = ask("cross.denied", "req-5", CLIENT_A);
  ui = applyAskToUi(ui, denied.status, denied.body);
  if (ui.phase !== "denied" || ui.answer !== null || ui.citations.length !== 0) throw new Error("J4");
  phasesSeen.add("denied");
  if (ENTERPRISE_A && ENTERPRISE_B_DOC) {
    const citationDenied = { status: 404, body: { detail: "MATERIAL_CITATION_NOT_FOUND" } };
    ui = applyAskToUi(ui, citationDenied.status, citationDenied.body);
  }

  const first = ask("provider.shared", "req-6", null);
  const replay = ask("provider.shared", "req-6", null);
  if (JSON.stringify(first) !== JSON.stringify(replay)) throw new Error("J5-replay");
  const conflict = ask("client.current", "req-6", CLIENT_A);
  ui = applyAskToUi(ui, conflict.status, conflict.body);
  if (ui.phase !== "conflict" || ui.citations.length !== 0) throw new Error("J5-conflict");
  phasesSeen.add("conflict");
  deletedClientA = true;
  const afterDelete = ask("client.current", "req-7", CLIENT_A);
  if (afterDelete.body.citations.length !== 0) throw new Error("J5-delete");

  ui = applyAskToUi(ui, first.status, first.body);
  const failed = ask("fail.clear", "req-8", null);
  ui = applyAskToUi(ui, failed.status, failed.body);
  if (ui.phase !== "unavailable" || ui.answer !== null || ui.citations.length !== 0) {
    throw new Error("J6-clear");
  }
  phasesSeen.add("unavailable");
  const waiting = ask("progress.wait", "req-9", null);
  const waitingUi = applyAskToUi({ ...ui, answer: "keep-on-in-progress", citations: [{ document_record_id: PROVIDER_DOC }] }, waiting.status, waiting.body);
  if (waitingUi.phase !== "in-progress") throw new Error("J6-progress");
  phasesSeen.add("in-progress");
  phasesSeen.add("empty");
  phasesSeen.add("ready");
  if (waitingUi.answer !== "keep-on-in-progress") throw new Error("J6-progress-keep");

  const payload = {
    journeys_passed: 6,
    cleared_on_failure: ui.answer === null && ui.citations.length === 0,
    phases: [...phasesSeen].sort(),
    phase_copy_keys: Object.keys(PHASE_COPY).sort(),
    enterprise: ENTERPRISE_A,
  };
  assertNoLeak(payload);
  assertNoLeak(j1);
  assertNoLeak(j2);
  assertNoLeak(comboA);
  assertNoLeak(comboB);
  if (payload.phase_copy_keys.length !== 10) throw new Error("phase-copy");
  process.stdout.write(`${JSON.stringify(payload)}\nLOCAL_MATERIAL_RAG_UAT_BROWSER_GATE_OK\n`);
}

main();
