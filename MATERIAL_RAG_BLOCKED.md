# MATERIAL RAG Blocked

## 2026-08-18 03:22｜合同漏项检查点｜现役

- 无。

当前状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY / NOT_PRODUCTION`。
`ARK_KEY_ROTATION_REQUIRED`。UAT 未授权。旧 20260817 检查点只读。

## 2026-08-18 03:14｜合同漏项检查点｜历史（开工，已被 03:22 收口）

- 当时只承认 `SMOKE_PASSED / CHECKPOINT_REPRODUCIBLE / NOT_PRODUCTION`，待修 wave2/3/4 head 断言与不可哈希 target。
- 已被本窗口任务1/2与新检查点收口，不再现役。

## 2026-08-18 00:30｜clone verify 1/3｜历史（已被 2/3 收口）

- 当时固定码：`LOCAL_MATERIAL_RAG_P3_UPLOAD_HTTP_FAILED`。墙钟 `1074908.427ms`，exit 2，stdout 空，`IMAGE_SOURCE=MATCH`，verifier `exited/1` `oom_killed=false`，其余专属服务 running/healthy。cleanup 后 C/V/N=0，共享 15 exited UNCHANGED。
- 含义：ASGI `POST /api/v1/ingestion/documents` 未返回 202；CRM 已 201，不是迁移 head 停在 0014。未扩 GRANT，未改 verifier/P3。
- 已用不同条件收口：暖镜像重建 clone 后 verify 2/3 exit 0、`LOCAL_MATERIAL_RAG_VERIFY_OK`。保全 `clone-verify-1/`。
- 当时窗口状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / NOT_PRODUCTION`。已被 2026-08-18 02:20 收口，不再现役。

## 2026-08-17 22:38｜parser 收紧窗口｜历史（切片 smoke 已过）

- 本窗口无 material-RAG 切片 smoke blocker。完整 `./scripts/localctl material-rag-verify` exit 0，stdout 恰为 canonical metrics JSON + `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH`。
- 上一窗现役 `OUTPUT_INVALID` 已关闭：pair 外只允许严格 `COMPOSE_*`；v3 接受 Compose 副本行 `material-rag-verifier-1 exited with code 0`。未把 OTHER/额外 JSON/额外 VERIFY_OK 当成功。未扩 GRANT。
- 保全：`/private/tmp/anhuan-material-rag-longrun-20260817/v1`–`v3`（0700/0600）。v3 根锚 `0f5ecc0091ba47d45263eea962a8b6ac52d36771530ab4b2366ca2f9459a5b7c`。
- 仍禁止：真实客户数据、生产写入、部署、commit/push、`RELEASE_VERIFIED`。Ark key 未打印、未修改。
- 未验证：UAT、生产、正式发布、key 轮换、共享 `anhuan-f1` 作为证据。

当前状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION`。已被 2026-08-18 检查点窗口收口，不再现役。

## 2026-08-17 20:00｜冲突探针窗口｜历史（verify 10/10 额度耗尽，已被 longrun v3 收口）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_OUTPUT_INVALID`
- 完整因果签名（v10）：`LOCAL_MATERIAL_RAG_OUTPUT_EVIDENCE {"envelope_count":0,"line_classes":"OTHER|JSON|VERIFY_OK|OTHER","line_count":4,"mismatch":"LINE_COUNT","mismatch_key":"NONE"}`
- 含义：IMAGE_SOURCE=`MATCH`。verifier 容器 exit 0，stdout 含 canonical JSON 与 `LOCAL_MATERIAL_RAG_VERIFY_OK`。localctl 在 cleanup 之后按「恰好两行」读 stdout 失败。前后各一行 `OTHER`，不是已登记的 `Aborting on container exit...` / `Container … exited with code 0`。INDEX/REPLAY/检索/rebuild job 已在 verifier 内走完；不能把这行写成业务 `REBUILD_FAILED`。
- 已排除：IMAGE_SOURCE MISMATCH；OOM（verifier `oom_killed=false` 的先前轮次）；cleanup 残留；共享 `anhuan-f1` 漂移；`f1_api` 写 unit；粘性 `DB_SNAPSHOT_EXIT` 作为本轮首因；CHUNK_ADD 作为现役首因。未扩 GRANT。
- 当时已落地、下一窗已执行：从 stdout 抽取唯一 JSON+`VERIFY_OK` 对；信封扩到小写 `container`、`Attaching to`、`Exited (0)`；先剥 ANSI。禁止把任意 `OTHER` 当成功。禁止改成功判据/canonical metrics。
- 保全：`/private/tmp/anhuan-material-rag-conflict-20260817/v1`–`v10`（0700/0600）。v10 根锚 `8eedddbe7b6b94e7fd9ece338351d7b70717339f9da02b75239bf861d6accf14`。
- 其余不确定项：两行 `OTHER` 的精确 Compose/TTY 文本（禁止保存正文）；longrun v1–v3 已用结构分类关闭，无需保存正文。
- 当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口。

## 2026-08-17 14:40｜known-version snapshot｜历史（上一窗口同一签名第三次停止）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INTERNAL_ERROR`
- 完整因果签名（v1=v2=v3 证据字节相同 `21c8fccb71a3c3691549cef35ab8d61f9e918976fd8964177c5260921e7776e7`）：`LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE {"db_token":"NONE","error_class":"DB_PROGRAMMING","operation":"DB_SNAPSHOT_EXIT","phase":"PJ_INDEX_REPLAY","primary_preserved":true,"sqlstate":"42501"}`
- 含义：PRIMARY_INDEX/ATTEST 仍过。known-version 方案使 operation 从旧窗 `DB_SNAPSHOT` 变为 `DB_SNAPSHOT_EXIT`。LOAD 阶段未作为失败 operation 出现。失败被记在 snapshot 会话退出。scope 级 SELECT 不是已确认根因。
- 已用两次不同修复仍相同：① 每 known version 独立 `session_scope`；② LOAD 内显式 `rollback` + unwrap DB 分类落到 SNAPSHOT_* INDEX checkpoint。第三次证据字节不变。
- 已排除：EMPLOYEE vs ADMIN 作为充分因；全 scope DISTINCT；CHUNK_ADD/egress 作为当前首因；OOM；cleanup 残留；共享漂移。未扩 GRANT。
- 唯一下一动作：下一授权窗口先在 cleanup 前核对 verifier 镜像内 `/app/infra/f1/local_material_rag_verify.py` 是否含本窗第二批标记（`await session.rollback()` / `_unwrap_internal_errors`）。若镜像落后于宿主，只允许用现有 `compose build` 路径让 COPY 层更新，不改成功判据、不改 Dockerfile/Compose。若镜像已同步，则 replay 身份对账改为内存指纹 + 已证 `_load_version_units`，snapshot helper 不再走额外会话。禁止扩 GRANT/BYPASSRLS。
- 保全：`/private/tmp/anhuan-material-rag-autonomy-20260817/v1`–`v3`（0700/0600）。v3 根锚 `091710cf88711a0f3a36ff12a11ecc0dd7f21bb0a87cf1d5c7c2773e71799ab2`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 04:53｜egress 分流｜历史（上一窗口同一 42501 第三次停止）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INTERNAL_ERROR`
- 完整因果签名（v3=v4=v5 字节相同）：`LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE {"db_token":"NONE","error_class":"DB_PROGRAMMING","operation":"DB_SNAPSHOT","phase":"PJ_INDEX_REPLAY","primary_preserved":true,"sqlstate":"42501"}`
- 含义：PRIMARY_INDEX 与 PRIMARY_ATTEST 已过。`_process_jobs` 进入 `PJ_INDEX_REPLAY` 后第一次 `_scope_unit_db_snapshot(client_a)` 以 `f1_api` 读 `material_rag_unit` 得到 42501。PostgreSQL 无匹配 RLS policy 是 0 行不是 42501；禁止扩 GRANT/BYPASSRLS。
- 已用两次不同证据化修复仍相同：① `ADMIN_SUB` + 去掉 `created_at` + `enterprise_id` 谓词；② 改为 `SELECT DISTINCT document_version_id` 再 `load_units_for_version`。第三次仍相同，按任务书停止。
- 已排除：EMPLOYEE vs ADMIN 作为充分因；`created_at` 列；CHUNK_ADD/egress/unauthorized/upstream_4xx 作为当前首因；FAILURE_EVIDENCE OOM；cleanup 残留；共享漂移。`load_units_for_version` 在 PRIMARY_INDEX 期间对单 version 可 SELECT 密文。
- 唯一下一动作：下一授权窗口不要再对 `material_rag_unit` 做 scope 级 SELECT（含 DISTINCT）。用 PRIMARY_INDEX 已在内存的 `persisted_by_version` 键，只按 version 调用已证查询 `load_units_for_version` 做指纹。冲突探测同样只对已知 version。禁止扩 GRANT、禁止弱化 FORCE RLS。
- 保全：`/private/tmp/anhuan-material-rag-egress-20260817/v1`–`v5`（0700/0600）。v5 根锚 `d427ae732994eea0f55c05aa51d74e69a2eab219c3dd0ef48db5805f1446cc24`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`（本窗口 canary 只报 `STATUS_CLASS`，未打印 key）

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 02:48｜finish RLS｜历史（上一窗口 v5 止损）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（本窗口 v5）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"NONE","job_status":"retry_wait","lease_live":false,"lease_present":false,"lease_source":"NONE","operation":"CLAIMED_SESSION","outcome":"FINISH_TRUE","phase":"PJ_PRIMARY_INDEX","reason_token":"CHUNK_ADD_CODE_400","token_match":false}`
- 含义：围栏与 finish RLS 已通。chunk add 的 RAGFlow 业务码为 400（HTTP 仍 200）。INDEX 失败发生在 FINAL_AUDIT 之前，本轮看不到 egress 计数。
- 已排除：job SELECT 42501；围栏 `MATERIAL_VERSION_NOT_INDEXABLE`；`MATERIAL_RAG_PROVISION_FAILED`；`MATERIAL_RAG_NETWORK_FAILED`；HTTP 401/403；code 字符串 `"0"`；FAILURE_EVIDENCE OOM（各服务 `oom_killed=false`）；cleanup 残留；共享 `anhuan-f1` 15 exited 未变；infected；fixture SHA 变化。
- 未改：fixture/allowlist、Ark endpoint/model、Docker 内存、GRANT/BYPASSRLS、`FOR SHARE OF active_job, task`。
- 唯一下一动作：下一授权窗口先在 INDEX 失败路径只发出 egress 计数（`rejected_unauthorized_text_count` / `rejected_json_count` / `rejected_non_text_input_count` / `upstream_4xx_count` / `authorized_embedding_request_count`），不要猜 payload。若 unauthorized_text>0，再单独授权把 RAGFlow v0.26.4「文档名+chunk 正文」拼接文本写入 embedding allowlist（`remote_document_name` 注释已写明会一并 embedding；当前只分别哈希了名字与 unit body）。若 upstream_4xx>0：停在 Ark/relay，不改 endpoint/model，不在窗口内轮换 key。
- 保全：`/private/tmp/anhuan-material-rag-finish-rls-20260817/v1`–`v5`（0700/0600）。v5 根锚 `7be267bf6dc7a30004cb3edc1cc73f25f1218cf3831a05e39de09829cf4b9314`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 02:40｜finish RLS｜历史（v4）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（本窗口 v4）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"NONE","job_status":"retry_wait","lease_live":false,"lease_present":false,"lease_source":"NONE","operation":"CLAIMED_SESSION","outcome":"FINISH_TRUE","phase":"PJ_PRIMARY_INDEX","reason_token":"CHUNK_ADD_FAILED_200","token_match":false}`
- 含义：chunk add HTTP 200，共享 client 把 `code != 0`（含字符串 `"0"`）当成失败。
- 已排除：HTTP 401/403；42501；围栏空行；Provision/OSError；FAILURE_EVIDENCE OOM；cleanup 残留；共享漂移。
- 已落地、待 v5：`_add_chunk_closed` 接受 0/`"0"`；非零业务码映射 `CHUNK_ADD_CODE_*`。禁止盲 retry、改 Docker 内存、扩 GRANT。
- 保全：v4 根锚 `0ffaf96979ea503c064f9fe121f97caa19179b7a3f8006d64147537873818ca5`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 02:33｜finish RLS｜历史（v3）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（本窗口 v3）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"NONE","job_status":"retry_wait","lease_live":false,"lease_present":false,"lease_source":"NONE","operation":"CLAIMED_SESSION","outcome":"FINISH_TRUE","phase":"PJ_PRIMARY_INDEX","reason_token":"MATERIAL_RAG_PROBE_FAILED","token_match":false}`
- 含义：`RagFlowProbeError`（非 Provision/OSError）。围栏与 job finish RLS 已越过。
- 已排除：42501；`MATERIAL_VERSION_NOT_INDEXABLE`；`MATERIAL_RAG_PROVISION_FAILED`；`MATERIAL_RAG_NETWORK_FAILED`；FAILURE_EVIDENCE OOM；cleanup 残留；共享漂移；Ark 固定码；infected；fixture SHA 变化。
- 已落地、待 v4：把 probe `reason`+白名单 HTTP status 写成 INDEX reason_token。若为 401/403：停止，不修。禁止盲 retry/timeout/扩 GRANT。
- 保全：v3 根锚 `1a64fb3ba7c2ce2584c4e57ed304047d3f1f0d0dc6e4fb9060f56de55dcc9b86`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 02:25｜finish RLS｜历史（v2）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（本窗口 v2）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"NONE","job_status":"retry_wait","lease_live":false,"lease_present":false,"lease_source":"NONE","operation":"CLAIMED_SESSION","outcome":"FINISH_TRUE","phase":"PJ_PRIMARY_INDEX","reason_token":"MATERIAL_RAG_UNAVAILABLE","token_match":false}`
- 含义：围栏已过；`finish_job` 把 job 写成 `retry_wait`。`MATERIAL_RAG_UNAVAILABLE` 仍是 `RagFlowProbeError|RagflowProvisionError|OSError` 四类桶。`CLAIMED_SESSION` 是 ContextVar 残留，不能单独当 DB 会话失败证明。
- 已排除：job SELECT 终态 42501；围栏 `MATERIAL_VERSION_NOT_INDEXABLE`（v1，已被 upload_task UPDATE policy 越过）；OOM 作为 FAILURE_EVIDENCE 首因（各服务 `oom_killed=false`）；cleanup 残留；共享漂移；Ark 固定码；授权/egress/跨租户/引用泄漏；infected；fixture SHA 变化。
- 已落地、待 v3：worker 拆成 `PROBE_FAILED`/`PROVISION_FAILED`/`NETWORK_FAILED` 三枚举。禁止盲 retry/timeout、禁止改 Docker 内存、禁止扩 GRANT。
- 保全：v1 根锚 `6178634876075009d613ab62e742ae3d4cab2df828f66109437be0ee547611a5`；v2 根锚 `76fbbc49eaf804eae8ccfcbbd054743fb23f6b9e16963deee43ca012cc03ff73`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 02:18｜finish RLS｜历史（v1，已越过）

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（本窗口 v1）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"NONE","job_status":"failed","lease_live":false,"lease_present":false,"lease_source":"NONE","operation":"MUTATION_FENCE","outcome":"FINISH_TRUE","phase":"PJ_PRIMARY_INDEX","reason_token":"MATERIAL_VERSION_NOT_INDEXABLE","token_match":false}`
- 含义：`finish_job` 已成功把 job 写成 `failed`（SELECT 终态新行假设成立）。围栏 JOIN 空行（或 `_released_sync` 失败）后以 `MATERIAL_VERSION_NOT_INDEXABLE` 收尾。lease 已释放。
- 已排除：缺 EXECUTE；job UPDATE WITH CHECK 42501；repository 重复 GUC 预置；OOM/137；cleanup 残留；共享 `anhuan-f1` 漂移；Ark 401/403/key/quota/TLS/DNS；授权/egress/跨租户/引用泄漏码；infected；fixture SHA 变化。
- 已落地、待 v2 证伪：`upload_task` 增加 lease-scoped `FOR UPDATE` policy（复用 SELECT 谓词）。禁止 `USING(true)`、禁止 worker `GRANT UPDATE`、禁止 session `f1.enterprise_id`、禁止弱化 `FOR SHARE OF active_job, task`。
- 保全：`/private/tmp/anhuan-material-rag-finish-rls-20260817/v1`。根锚 `6178634876075009d613ab62e742ae3d4cab2df828f66109437be0ee547611a5`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-17 00:32｜一次性打通｜finish_job 42501 复现后停止

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 完整因果签名（v1=v2 证据字节相同）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","finish_sqlstate":"42501","job_status":"running","lease_live":true,"lease_present":true,"lease_source":"NONE","operation":"MUTATION_FENCE","outcome":"FINISH_EXCEPTION","phase":"PJ_PRIMARY_INDEX","reason_token":"NONE","token_match":false}`
- 含义：`process_demo_job` 走到 `finish_job` 后抛 `FINISH_EXCEPTION`/`42501`；job 仍为 `running`，lease 仍在且未过期，`error_reason` 仍空。不是 LeaseLost 黑洞，不是 137/OOM，不是 INTERNAL。
- `operation=MUTATION_FENCE` 是 verifier ContextVar 残留（进入 fence 后未清），不能当成 FOR SHARE 语句本身仍在报 42501 的证明。
- 已证伪：在 `finish_job` 会话预置 material-rag GUC。v2 INDEX 行 SHA256 与 v1 相同。
- 已排除：OOM/137；cleanup 残留；共享 `anhuan-f1` 漂移；Ark 401/403/key/quota/TLS/DNS；授权/egress/跨租户/RLS/引用泄漏码；infected；fixture SHA 变化。
- 唯一下一动作：在白名单内针对 `f1.finish_material_rag_job` 把 `running` 写成 `done` 时的 `42501`（RLS WITH CHECK `material_rag_job_worker_update` 或该 UPDATE 触及的固定权限）做可证伪最小修。允许改 `f1_0015` 的 job UPDATE policy 表达式。禁止给 worker 加 GRANT/BYPASSRLS，禁止弱化 `FOR SHARE OF active_job, task`，禁止再盲加 retry/timeout/lease/内存/session GUC。
- 保全：`/private/tmp/anhuan-material-rag-closeout-20260817/v1` 与 `v2`（目录 0700，文件 0600）。v1 根锚 `63b4203c5d16b766d364e2adf2e150e9fe67dac6216a706dff5e6c69c9967832`。v2 根锚 `f142fdd1b725e006bcb94760cf2586057e74933f4112703e944fe65a95bc1279`。
- 其余不确定项：无
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-16 20:40｜自治收口｜同一 INDEX 固定原因复现后停止

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`
- 证据（v1=v2 字节级相同）：`LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"checkpoint":"PRIMARY_PROCESS","job_status":"running","operation":"MUTATION_FENCE","phase":"PJ_PRIMARY_INDEX","reason_token":"NONE"}`
- 含义：`process_demo_job` 返回 False；job 仍为 `running` 且 `error_reason` 为空。不是 42501，不是 INTERNAL。
- 已排除：OOM/137；cleanup 残留；共享 `anhuan-f1` 漂移；Ark 401/403/key/quota/TLS/DNS；授权/egress/跨租户/RLS/引用泄漏码；infected；fixture SHA 变化；task 锁 42501。
- 阶段 A 已完成：fence 恢复为 `FOR SHARE OF active_job, task`（不锁 version/record）；INDEX 独立 buffer，redirect 结束后 emit；目标 unittest `Ran 1 / OK`。
- 阶段 B：授权 4 次 verify，已用 2 次。v1 墙钟 `196.749s` exit 2。v2 在围栏事务补 `f1.enterprise_id` 后墙钟 `203.342s` exit 2，固定证据不变，已撤回该 GUC。按「同一固定原因修复后再次复现」停止，未跑 v3/v4。
- 保全：`/private/tmp/anhuan-material-rag-closeout-20260816/v1` 与 `v2`（目录 0700，文件 0600，含 stdout/stderr/exit/wall/evidence/C-V-N/shared/manifest）。
- 下一动作：针对 `MUTATION_FENCE` 上 `PRIMARY_PROCESS`+`running`+`reason_token=NONE`（LeaseLost 或 `finish_job` 未落盘）做新的可证伪取证后再授权 verify。不要弱化 `FOR SHARE OF active_job, task`，不要扩 grant/BYPASSRLS，不要盲加 retry/timeout/内存。
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-16 17:49｜120分钟自治收口｜6次verify用尽

- 本窗口唯一固定 blocker：`LOCAL_MATERIAL_RAG_INDEX_FAILED`（v5/v6）
- 证据：v6 `LOCAL_MATERIAL_RAG_INDEX_EVIDENCE_DEGRADED MISSING`。verifier 在 `redirect_stderr` 内打印 INDEX 行，localctl 看不到。
- 已排除：OOM/137；cleanup 非零；共享漂移；Ark 认证/配额/网络固定码；授权或隔离失败码；当轮 INTERNAL。
- 已越过的精确故障：`SCOPE_LOCK`/`TEXT_NUL`；`MUTATION_FENCE`/`42501`。
- 唯一下一动作：把 INDEX 证据改成与 INTERNAL 相同的 buffer，在 `main()` 重定向结束后 emit；再授权 1 次 verify 读取 `job_status`/`reason_token`。不要盲加 retry/timeout/内存。
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-16 16:05｜asyncio + operation 窗口｜止损

- 本窗口 blocker：无
- 继承起点（不是本窗口止损）：`LOCAL_MATERIAL_RAG_INTERNAL_ERROR` / phase `PJ_PRIMARY_INDEX` / operation `PROCESS_DEMO_JOB` / error_class `DB_OTHER`
- 下一步：目标测试先红后绿，再跑 verify 1/6 取 sqlstate/db_token
- 禁止：checkout `codex/material-rag`；丢弃 dirty；读 Ark key；中途向用户汇报

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。

## 2026-08-16 16:05｜asyncio + operation 窗口｜止损

- 唯一固定 blocker：`LOCAL_MATERIAL_RAG_INTERNAL_ERROR`
- 阶段：`PJ_PRIMARY_INDEX`
- 操作：`PROCESS_DEMO_JOB`
- 错误类别：`DB_OTHER`
- 证据：`LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE {"error_class":"DB_OTHER","operation":"PROCESS_DEMO_JOB","phase":"PJ_PRIMARY_INDEX","primary_preserved":true}`
- 止损原因：已得到具体 phase+operation+error_class；本消息禁止同轮猜修，不得改业务代码或重跑 verify。
- 已排除：阶段/operation 为 `UNKNOWN`；证据畸形/重复；OOM/137；cleanup 非零；共享漂移；Ark 认证/配额/网络固定码；授权或隔离失败码。
- 当轮无 preview/scanner/provider/BUILD 行；未写成 NOT_REACHED。无 Ark 聚合审计计数。
- 唯一下一动作：在白名单内针对 `process_demo_job` 的 DB_OTHER（非 Operational/Integrity/Programming）做可证伪最小修，再授权 1 次 verify。不要盲加 retry/timeout/内存。
- `ARK_KEY_ROTATION_REQUIRED`（本窗口仅 lstat，未读、未打印、未轮换）。

当时窗口状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。已被 2026-08-17 22:38 收口，不再现役。
