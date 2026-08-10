# P6 AUTOMATED QUALITY / SYNTHETIC ORACLE 任务书

> **已完成的历史执行合同。** P6 当前为 `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文保留启动基线。

## 状态与边界

- P5已收口为`P5_COMPLETE_NOT_RELEASE_VERIFIED + TARGETED_TEST_PASSED`；P6在同一未提交工作树继续。
- 唯一线性迁移为`f1_0009_automated_quality`，`down_revision=f1_0008`；此前迁移只读，禁止第二Alembic head。
- P6只做内部质量套件、合成场景、确定性Oracle、run/result、分歧与人工处置；不创建Gold、不调用外部OCR/LLM/provider、不声称真实准确率或专业质量。
- 固定边界：`SYNTHETIC_ORACLE_ONLY / NON_GOLD / ACCURACY_NOT_EVALUATED / NO_EXTERNAL_MODEL_CALLS / NOT_PRODUCTION`。
- 不恢复F1.1.1发布验收；不跑全仓/E2E/coverage/benchmark/生产build；不commit、不push、不部署、不删除共享数据。

## 产品闭环

创建质量套件 → 登记合成场景与确定性Oracle → 触发本地run → 生成不可变result和证据摘要 → 识别parser/OCR/citation/refusal/injection/authorization分歧 → 人工确认或豁免 → 驾驶舱查看趋势。

## Task 0：合同与并行地界

- 主agent单写任务文档、`f1_0009`、`models.py`、API main、App/Layout和共享文件。
- Backend子任务只新增`src/platform_foundation/f1/features/p6/**`和`src/platform_foundation/f1/api/routers/p6_automated_quality.py`。
- Frontend子任务只新增`src/web/src/features/p6/**`；最后由主agent接App/Layout。
- 五张新表均企业复合FK、`ENABLE + FORCE RLS`、PUBLIC/worker无权；结果append-only，跨租户详情404/集合零行；不新增SECURITY DEFINER。
- Oracle只接受有限结构化数值/布尔/枚举和SHA，不存正文、问题文本、文件名、路径、PII、token或object URL。

## Wave 1：质量套件与合成场景

- `quality_suite`：name、category(`ingestion/retrieval/qa/authorization/injection`)、status(`active/archived`)、creator/time。
- `quality_scenario`：suite、scenario_key、scenario_type(`exact_match/threshold/refusal_required/isolation_required/injection_blocked/disagreement_max`)、severity(`low/medium/high/critical`)、oracle_config JSONB、synthetic_observation JSONB、enabled、creator/time。
- JSON只允许深度受限的object与数字/布尔/固定短字符串；每项≤16KiB，服务端canonicalize并计算scenario SHA；禁止任意正文载荷。
- API：`GET/POST /suites`、`GET /suites/{id}`、`POST /suites/{id}/scenarios`、`PATCH /scenarios/{id}`。
- 页面：`/quality`、`/quality/suites/:suiteId`。

## Wave 2：确定性run与不可变result

- `quality_run`：suite、status(`queued/running/passed/failed/cancelled`)、trigger_kind=`manual`、总数、创建/开始/完成时间。
- `quality_result`：run、scenario、status(`passed/failed/error`)、reason_code、observed_metrics JSONB、evidence_sha256、created_at；禁止UPDATE/DELETE。
- 同一事务锁suite/scenarios，run从queued→running，纯本地Oracle逐项计算后写results并终结run；无网络、OCR、LLM、RAGFlow或provider调用。
- API：`POST /suites/{id}/runs`、`GET /runs/{id}`；页面`/quality/runs/:runId`。

## Wave 3：分歧与人工处置

- `quality_disagreement`：result、kind(`parser/ocr/citation/refusal/authorization/injection`)、left_digest/right_digest、score、review_status(`open/acknowledged/waived`)、review_note、reviewed_by/time。
- result失败且scenario声明disagreement kind时同事务生成分歧；digest仅SHA，不存两侧正文。
- 仅管理员或auditor可acknowledge/waive，不能把failed result改成passed；处置与audit同事务。
- API：`GET /disagreements`、`PATCH /disagreements/{id}`；页面`/quality/disagreements`。

## Wave 4：质量驾驶舱与集中接缝

- `GET /dashboard`返回suite/run/result/disagreement聚合和最近run；所有数字仅标“合成场景”。
- 页面按钮只看`allowed_actions`，企业切换abort/清空；含加载/错误/空态、桌面表格和窄屏卡片。
- 仅修启动/编译、主链阻断、数据损坏、明确跨租户越权、migration head冲突；其余登记BLOCKED。

## 验证与状态

- P6最多一个预计60秒内的直接相关检查，覆盖`f1_0009 → f1_0008`、Oracle纯函数、result不可变、分歧不能改判、API/页面合同。
- 未运行标`NOT_TESTED`；通过仅`TARGETED_TEST_PASSED`。完成标签`P6_COMPLETE_NOT_RELEASE_VERIFIED`，禁止`RELEASE_VERIFIED`。
