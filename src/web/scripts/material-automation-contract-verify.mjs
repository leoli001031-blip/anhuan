import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const panel = readFileSync(`${webRoot}src/components/MaterialPanel.tsx`, "utf8");
const api = readFileSync(`${webRoot}src/features/p3/ingestionApi.ts`, "utf8");

assert.match(
  panel,
  /key === "report" && stage\.status === "ready"\) return "草稿已生成"/,
  "report-ready must be presented as a draft, not as published",
);
assert.match(panel, /这不代表已审核或已发布/);
assert.match(panel, /`\/console\/clients\/\$\{encodeURIComponent\(clientAccountId\)\}\/reports`/);
assert.match(panel, /REPORT_REVIEW_REQUIRED/);
assert.match(panel, /去审核/);

assert.match(panel, /pipelineState !== "failed"/);
assert.match(panel, /setPipelineNonce\(\(value\) => value \+ 1\), 5000/);
assert.match(panel, /message="流水线状态暂不可用"/);
assert.match(panel, /message="材料详情加载失败"/);
assert.match(panel, /setAnalysisNonce\(\(value\) => value \+ 1\)/);
assert.match(panel, /version\.allowed_actions\.includes\("process"\)/);
assert.match(panel, /\? processIngestionVersion/);
assert.match(panel, />重新处理<\/Button>/);

assert.match(panel, /closable=\{!uploading\}/);
assert.match(panel, /maskClosable=\{!uploading\}/);
assert.match(panel, /cancelButtonProps=\{\{ disabled: uploading \}\}/);
assert.match(panel, /列表展示安全入库状态/);

assert.match(api, /query\.set\("client_account_id", clientAccountId\)/);
assert.match(api, /"\/auto-pipeline"/);

console.log("material automation frontend contracts: ok");
