# MATERIAL RAG Progress

## 2026-08-19｜stale 本地零写 + restore 维护原语｜PASSED

- 现役：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / MATERIAL_RAG_BACKUP_RESTORE_DESIGN_READY / BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED / BACKEND_CHECKPOINT_READY / NOT_PRODUCTION`。未写 `UAT_PASSED` / `RELEASE_VERIFIED`。未 commit/push、未更新 PR #2。
- 红：`MATERIAL_RAG_PGINT_CYCLE=restore-red-v1` `Ran 17 tests in 146.451s / FAILED (failures=1, errors=2)`。主缺口：`restore_maintenance_clear_lifecycle` 缺失。伴随：illegal 留下 maintain 远端 dataset，旧 `index_rebuild` 断言 `datasets==0` 失败；live lease 使 tearDown residual 失败。旧 raw `RAW_RED_GREEN_OUTPUT_NOT_CAPTURED` / `HARDENING_CYCLE_EVIDENCE_OVERWRITTEN` 仍不伪造。
- 一跳：illegal 末尾走合法 `delete` 清远端（不放宽旧断言）；harness 单事务 DELETE jobs→DELETE units→binding 清密文落 `deleted`。禁止非终态 UPDATE failed。
- 绿：`restore-green-v1` `Ran 17 tests in 146.760s / OK`，skipped=0，exit=0。合并：`restore-merge-v1` `Ran 106 tests in 147.901s / OK`，skipped=0，exit=0。打印 `LOCAL_MATERIAL_RAG_JOB_RECOVERY_OK` 但不单独当绿。`git diff --check=0`。
- 建议替换：lease 到期仍墙钟等待，不 SQL 拨 `lease_until`。cycle 证据改为存在即拒（`CYCLE_EVIDENCE_EXISTS`），避免复用覆盖。
- 预算：专属 Docker 3/3；C/V/N=0；共享 fingerprint 不变。生产三文件零 diff。证据包 `material-rag-backup-design-hardening-20260819-v1`。

## 2026-08-19｜stale 本地零写 + restore 维护原语｜STARTED

- 目标：stale claim 对本地库也零写；backup/restore 离线维护符合 f1_0015 trigger；只到 `BACKEND_CHECKPOINT_READY`。不 commit/push、不更新 PR #2。
- 顺序：任务0 核基线并降 backup 标签 → 先补断言取红 → harness-only 维护转绿 → 合并门。
- 最大风险：DELETE 维护撞 RLS/权限/trigger；stale 本地 snapshot 暴露生产写。只记 blocker，不改生产。
- 预算：最多 3 个专属 Docker，cycle=`restore-red-v1` / `restore-green-v1` / `restore-merge-v1`，不复用覆盖。
- 开工核：branch=`codex/material-rag-postgres-integration`；HEAD=`0a094e27ede877a4661aa1dbaa03846404ac567d`；6M+1??、staged=0、`git diff --check=0`；生产三文件零 diff；PR #2 draft；hardening MANIFEST sha=`d97b8f0b60d60148158e454702c25d7ac3c99d1f24c55db066ebc9c79ef7eac3`。
- 历史 `RAW_RED_GREEN_OUTPUT_NOT_CAPTURED` / `HARDENING_CYCLE_EVIDENCE_OVERWRITTEN` 保留，不伪造补齐。现役 backup 先降为 `BACKUP_RESTORE_DESIGN_NEEDS_REVISION`。建议替换：无。

## 2026-08-19｜生命周期证据门加固｜PASSED

- 现役恢复：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_READY / NOT_PRODUCTION`。另写 `MATERIAL_RAG_BACKUP_RESTORE_DESIGN_READY / BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED`。未写 `UAT_PASSED` / `RELEASE_VERIFIED`。未 commit/push、未更新 PR #2。
- 红：`MATERIAL_RAG_PGINT_CYCLE=hardening-lifecycle` `Ran 15 tests in 85.696s / FAILED (failures=2)`。`create_dataset_commit_then_drop` 未落库即返回 `SUCCESS`；`orphan_unit` 硬编码 0。旧 raw `RAW_RED_GREEN_OUTPUT_NOT_CAPTURED`。
- 中红：补 fake/SQL 后一次 `Ran 15 / FAILED (failures=1, errors=1)`：检索夹具 `stale` 全局被算 orphan。一跳：residual 的 unit/binding 门改按 lifecycle scope 计数。
- 绿：同一命令 `Ran 15 tests in 84.775s / OK`，skipped=0。合并门 `Ran 104 tests in 86.438s / OK`（>101）。`git diff --check=0`。打印 `LOCAL_MATERIAL_RAG_JOB_RECOVERY_OK`。
- 建议替换：① unit trigger 禁止 bootstrap 插入非法 unit，改用同一事务 `UPDATE upload_task.content_sha256` 使已有 unit 与上游 source 脱钩后 rollback；② residual 的 orphan/provisioning/deleted-secrets 限定 lifecycle scopes，因同一库检索夹具 `stale` 故意 source 脱钩；idle/live_lease 仍全局；③ lease 到期仍墙钟等待，不 SQL 拨 lease。
- 预算：专属 Docker 4/4；C/V/N=0；共享 fingerprint 不变。生产三文件零 diff。证据包 `material-rag-job-lifecycle-hardening-20260819-v1`。

## 2026-08-19｜生命周期证据门加固｜STARTED

- 目标：封远端 rebuild/delete、stale lease、真实 residual、restore 设计顺序四类假绿；不改生产、不 restore、不更新 PR #2。
- 顺序：任务0 核基线并降级 → 先补断言取红 → 只改 fake/harness 转绿 → 修设计 → 合并门 >101。
- 最大风险：新断言暴露生产缺陷（本轮只记 blocker 不修）；误覆盖旧证据包。
- 预算：4 小时、4 个专属 Docker、12 条目标检查、5 个最小修改批。证据真实优先。
- 开工核：branch=`codex/material-rag-postgres-integration`；HEAD=`0a094e27ede877a4661aa1dbaa03846404ac567d`；6M+1??、staged=0、`git diff --check=0`；生产三文件零 diff；PR #2 draft、base=`codex/material-rag-scanner-protocol`。旧 raw 记 `RAW_RED_GREEN_OUTPUT_NOT_CAPTURED`。建议替换：无。
- 现役先降为：`TARGETED_TEST_PASSED / BACKEND_JOB_LIFECYCLE_EVIDENCE_INCOMPLETE / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_NEEDS_REVISION / NOT_PRODUCTION`。

## 2026-08-19｜生命周期 / 已知ID重投 / backup 设计｜PASSED

- 目标已达：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_READY / NOT_PRODUCTION`。另写 `MATERIAL_RAG_BACKUP_RESTORE_DESIGN_READY / BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED`。未写 `UAT_PASSED` / `RELEASE_VERIFIED` / production。未二次 commit/push。
- 任务1（已过门，不重做）：HEAD=`0a094e27ede877a4661aa1dbaa03846404ac567d`，draft PR #2 base=`codex/material-rag-scanner-protocol` head=`codex/material-rag-postgres-integration`。PR #1 未动。
- 任务2红：周期1 `Ran 8 / FAILED (errors=1)`（测试 `setUpClass` 误用 `cls.assertIsNotNone`，非生产缺陷）。周期2 `Ran 12 / FAILED (failures=2)`：rebuild 快照取在 enqueue 之后故 `manifest_sha=None`；provision 与 recovery 共用 tenant B scope 以致 `create_dataset` 被跳过返回 `SUCCESS`。
- 任务2/3绿：周期3同一命令 `Ran 12 tests in 54.706s / OK`，skipped=0，打印 `LOCAL_MATERIAL_RAG_JOB_RECOVERY_OK`。周期4合并门 `Ran 101 tests in 55.392s / OK`（>97，skipped=0）。`git diff --check=0`。
- 真实路径：`enqueue_job → claim_job → process_claimed_demo_job → finish_job`；PostgreSQL 18.3、f1_0015、session_scope、RLS、trigger、advisory lock、加密 unit、manifest、lease/source fence。fake 仅 `RagFlowClient._request` 与 `redis.Redis.from_url`。未 mock worker/repository/session，未改 `worker.py`/`repository.py`/`ragflow_adapter.py`（红灯不是生产缺陷）。
- 建议替换：`make_retry_due`/`expire_running_lease` 用墙钟等待而非 SQL 拨 `lease_until`/`next_attempt_at`，因 `material_rag_guard_job` 会 `MATERIAL_RAG_JOB_TRANSITION_INVALID`。
- 预算：专属 Docker 4/4；共享 fingerprint 逐字节不变（cycle json `shared_fingerprint_match=1`，C=15 V=9 N=1）；专属 C/V/N=0。生产三文件未改。后续 dirty 未 commit/push。

## 2026-08-19｜PostgreSQL checkpoint 发布 + 生命周期门｜STARTED

- 目标：先安全发布当前 7 文件 PostgreSQL checkpoint（stacked draft PR），再用真实 worker/repository 完成任务生命周期与已知 job_id 重投恢复门，并只产出下一轮 backup/restore 设计。
- 顺序：任务0 核基线 → 任务1 精确 stage 七路径 commit/push/draft PR（不重跑 97/Docker）→ 任务2 红→绿生命周期 → 任务3 已知 ID 重投 → 任务4 设计文档。后续生命周期改动留本地 dirty，不再 commit/push。
- 最大风险：lease/source fence 或远端失败时本地提前删除；误动冻结的 f1_0015/RLS/默认 backup。
- 预算：6 小时、最多 4 个专属 Docker 周期、20 条目标检查、8 个最小修改批。隔离与租约安全优先。
- 开工核：branch=`codex/material-rag-postgres-integration`；HEAD=`c58ef92bde3086e26cbd119bbbb4debe6f7eb905`；远端仅 `codex/material-rag-scanner-protocol@c58ef92`；PR #1 为 `main <- codex/material-rag-scanner-protocol` draft。本会话初工作树被还原到 HEAD、3 新文件缺失；已从冻结 `worktree.patch` 恢复为 4M+3??，未重跑已过 97 门。专属 C/V/N=0。建议替换：无。

## 2026-08-19｜真实 PostgreSQL 后端集成门｜PASSED

- 工作仓：clean clone。HEAD=`c58ef92bde3086e26cbd119bbbb4debe6f7eb905`。branch=`codex/material-rag-postgres-integration`。未碰旧 recovery dirty checkout。本检查点已获 commit/push/draft PR 授权，OID/PR 以 GitHub 为准。未读 Ark secret。
- 最窄栈：compose 只含 `secret-init` + `postgres`；宿主跑 f1_0015 migrator/seed 与 unittest。fake 仅 remote transport。未启动完整 `material-rag-verify`。未改 `scripts/localctl`。未改 `ports.py`/`service.py`/`repository.py`/`contracts.py`。
- 红：`tests.test_material_rag_postgres_integration` 先 `Ran 1 / FAILED (errors=1)`（harness 未落地），栈健康后 `Ran 8 tests / FAILED (failures=3)`（conflict 测试在首次 retrieve 之后才 derive context，把合法 +2 IO 误算成副作用）。一跳：把 derive 移到首次 retrieve 之前。未改生产代码。
- 绿未单独重跑 integration-only（避免第 4 个 Docker 周期）。合并门一次：`Ran 97 tests in 8.998s / OK`，failures/errors/skipped=0。`git diff --check=0`。
- clean-clone：基线 `c58ef92` + 当前 patch，同一合并门一次 `Ran 97 tests in 8.877s / OK`。
- 三个专属 Docker 周期均 `COMPOSE_DOWN` 后 C/V/N=0、控制目录=0、共享 fingerprint 逐字节等于开工 before `f08864aa2b34d9ddc9f98f114590a0a8b58eeba0e7c5c7a989adf45881b0d065`（C=15 V=9 N=1，全部 exited）。
- 现役：`BACKEND_POSTGRES_REPOSITORY_INTEGRATION_PASSED / BACKEND_RLS_SCOPE_ISOLATION_PASSED / BACKEND_TRANSACTION_RECOVERY_PASSED / BACKEND_RUNTIME_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED / NOT_PRODUCTION`。未写 `UAT_PASSED` / `RELEASE_VERIFIED` / production。

## 2026-08-19｜真实 PostgreSQL 后端集成门｜STARTED

- 工作仓：clean clone。HEAD=`c58ef92bde3086e26cbd119bbbb4debe6f7eb905`。branch=`codex/material-rag-postgres-integration`。工作树开工 clean。未碰旧 recovery dirty checkout。未 commit/push。
- 远端基线：`origin/codex/material-rag-scanner-protocol` 同 commit。未读 Ark secret。现役 live retrieval 只记 `LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED`。
- 任务0：无并发 backend integration/UAT/verify。专属 C/V/N=0。共享 `anhuan-f1` canonical before fingerprint SHA256=`f08864aa2b34d9ddc9f98f114590a0a8b58eeba0e7c5c7a989adf45881b0d065`（C=15 V=9 N=1，全部 exited）。
- 选定最窄专属运行方式（写入后再启动）：compose 只含 `secret-init` + `postgres`；宿主跑 `infra/f1/material-rag/migrate.py`（f1_0015）与 `seed.py`；unittest 在宿主 venv。fake 仅 transport。不启动完整 `material-rag-verify`，不依赖 Demo PDF/Ark/共享栈。不改 `scripts/localctl`。
- 命令闭集：compose `infra/f1/docker-compose.material-rag-postgres-integration.yml`（secret-init + postgres）；宿主 migrate/seed；unittest `tests.test_material_rag_postgres_integration`；cleanup `compose down --volumes --remove-orphans` 后确认专属 C/V/N=0。
- 当时目标标签尚未写入。保持 `HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED / NOT_PRODUCTION`。

## 2026-08-19｜发布与 Ark 边界｜AUTHORIZED

- 用户已授权将本后端检查点提交并推送 GitHub；具体 commit/PR 以 GitHub 为准，不部署、不写生产。
- Ark key 轮换不再作为离线、本地后端及其他非 live 开发的 blocker。真实 live retrieval 继续记为 `LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED`，不得冒充通过，也不得使用未知或旧凭证。
- 现役：`BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`。

## 2026-08-19｜生产检索信任边界｜PASSED_BACKEND_GATE

- 从 checkpoint `6dd4b9158af3f8eb15922fff5bc715c9a3848f68` 出发。branch=`codex/material-rag-scanner-protocol`。未 checkout/stash/reset/clean/amend。未 commit/push。未跑 headed UAT / Ark / Docker / 部署。不重跑 checkpoint UAT。
- 离线合同先红 `Ran 11 tests in 0.410s / FAILED (errors=8)`，一跳后绿 `Ran 11 tests in 0.282s / OK`。中间一次 `Ran 11 / FAILED (failures=1)`：重复候选在校验前去重会丢掉后续合法副本；改为仅在成功写入证据后去重。
- 公共 `/api/v1/material-qa` 行为测试已绿，未改 `qa_service.py` / `material_qa.py`。未开放 Ark 或新公开接口。
- 主仓合并门一次：`Ran 89 tests in 1.581s / OK`，exit=0，failures/errors/skipped=0。`git diff --check=0`。
- clean-clone：原始 HEAD 隔离 clone，binary patch + 新文件，hash 全同后 chmod 对账 8/8 mismatch=0；clone 合并门一次 `Ran 89 tests in 1.628s / OK`。
- 现役：`BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `UAT_PASSED` / `RELEASE_VERIFIED` / production。checkpoint 机器门仅为历史证据。

## 2026-08-19｜生产检索信任边界｜STARTED

- 从 checkpoint `6dd4b9158af3f8eb15922fff5bc715c9a3848f68` 出发。branch=`codex/material-rag-scanner-protocol`。工作树 clean、staged=0、untracked=0。未 checkout/stash/reset/clean/amend。
- 本窗口只做纯离线后端合同门 + clean-clone 可重建。不跑 headed UAT，不连接 Ark/RAGFlow，不启动 Docker，不部署，不 commit/push。不重跑、不冒充 checkpoint UAT。
- 现役保持 `UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `BACKEND_*_PASSED`、`UAT_PASSED`、`RELEASE_VERIFIED` 或 production。
- 顺序：任务0基线（已核）→ 先写 `tests/test_material_rag_service_offline.py` 取一次红灯 → 最小抽取 ports → 绿灯 → 公共 QA 仅证据驱动 → 合并门一次 → clean-clone 一次。

## 2026-08-18 23:22｜租户 Select 时序/定位｜PASSED_MACHINE_GATE

- 现役：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `HUMAN_UAT_READY`。未 commit。Ark 外发=0。未跑 headed open。
- 离线：先红 `Ran 63 tests in 3.275s / FAILED (failures=6)`，后绿 `Ran 63 tests in 5.314s / OK`；一跳后再绿 `Ran 63 tests in 5.329s / OK`。failures/errors/skipped=0。`git diff --check=0`。白名单外 SHA 漂移=0。
- Live 周期1：start exit 0 wall=34510ms `http://127.0.0.1:62243/qa`。check exit 2 wall=61523ms。反向四门全 1。`UAT_TENANT_SWITCH_FAILED`（commit 等待，非 OPTION_MISSING）。`finally` 已 down。
- 一跳后 Live 周期2：start exit 0 wall=34217ms `http://127.0.0.1:63153/qa`。check exit 0 wall=8668ms。反向四门全 1；六旅程；J6 五字段全 1；`valid_tenant_count=2`；三项 cross-tenant；唯一 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。stop exit 0 wall=33477ms `C=0 V=0 N=0`。
- 收口专属 C/V/N=0，控制目录已删除，共享 before/after 字节相同 C=15/V=9/N=1。证据 `/private/tmp/anhuan-material-rag-tenant-switch-20260818` detached_root=`e9125ab16ef596c3bb019d287a69398f69b2350a96c139d5a938f04363770b64`。旧证据只读。根因仍不写成已确认产品 bug；本窗验证的是 header Select 提交判定（title 对 UUID）与定位。

## 2026-08-18 22:57｜租户 Select 时序/定位｜STARTED（已被 23:22 收口）

- 目标：验证 header Select 时序/定位竞态（未写成产品 bug）；反向红→最小修 runner；fresh live≤2 恢复机器门。不跑 headed open / Ark / commit。
- 顺序：任务0基线（已核）→ 定位假绿先红 → aria-controls 轮询+精确匹配+CDP 点击 → ≥58 全绿 → live start/check，成功才 stop。
- 最大风险：AntD6 无 aria-controls 契约时需唯一含目标 option 的可见 dropdown 替代；第2周期仅允许第1周期给出新固定证据并完成一跳后使用。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。unittest `Ran 58 tests in 3.170s / OK`。专属 C/V/N=0，无控制目录，无并发 UAT。共享 C=15/V=9/N=1。24 路径 mode/SHA 已登记 `/private/tmp/anhuan-material-rag-tenant-switch-20260818-whitelist.json`。
- 现役保持 `TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。20:24 机器门仍为历史。
- live 额度：本窗口 0/2。J6 clear/localctl 冻结。白名单外禁止改。
- 任务1红：`Ran 63 tests in 3.275s` / `FAILED (failures=6)`。签名：`tenantDisplayValue` 合同缺失、`SWITCH_FN_MISSING`×5（delayed-portal / leftover-qa / refuse-bad-options / commit-failed / steps-evidence）。未改测试绕过。
- 任务2绿：同一命令 exit 0，`Ran 63 tests in 5.314s` / `OK`，failures/errors/skipped=0。`git diff --check=0`。白名单外 SHA 漂移=0。实现：membership `name + role` 精确展示值；header `aria-controls` 轮询受控 listbox；精确 title/content；CDP 鼠标点外层 option；header+localStorage 双提交；步骤 A0/B1/A2/B3；无敏计数证据。未改 J6 clear/localctl。未采用 fallback（aria-controls 路径已进测试）。
- Live 周期1：start 23:14:05→23:14:40 wall=34510ms exit 0，`HUMAN_UAT_URL http://127.0.0.1:62243/qa`。check 23:14:40→23:15:41 wall=61523ms exit 2。反向四门全 1。浏览器 `UAT_TENANT_SWITCH_FAILED`（localctl 冻结未转印计数 JSON；61s≈commit 等待，不是 OPTION_MISSING）。`finally` 已 down，专属 C/V/N=0，未补 stop。
- 一跳：commit 改为 title 或可见 text 精确等于目标（避免 selection-item title 为 UUID 时假失败）；失败码编入步骤/action/计数以便冻结 localctl 转印；点击前 elementFromPoint 必须落在目标 option。离线复跑 `Ran 63 tests in 5.329s / OK`。准备周期2。

## 2026-08-18 22:45｜J6 同页清空｜STOPPED_LIVE_BUDGET

- 现役：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未恢复机器门两标签。未写 `HUMAN_UAT_READY`。未 commit。Ark 外发=0。本窗口 live 2/2 用尽，禁止第三次。
- 末次 live 固定码：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_OPTION_MISSING`。周期1 为 `UAT_TENANT_SWITCH_FAILED`。均无六键 JSON，J6 五字段未出现。不得把离线 `Ran 58 / OK` 写成 live 通过。
- 离线：先红 `Ran 58 / FAILED (failures=6)`，后绿 `Ran 58 tests in 3.034s / OK`。同页 prior→fail.clear、requestId 绑定、五键计算已进 runner/localctl。白名单外 SHA 漂移=0。
- Live 周期1：start exit 0 wall=34658ms `http://127.0.0.1:57636/qa`。check exit 2 wall=60926ms。反向四门全 1。`finally` 已 down。
- 一跳后 Live 周期2：start exit 0 wall=34828ms `http://127.0.0.1:58198/qa`。check exit 2 wall=41111ms。反向四门全 1。租户选项唯一可见 dropdown DOM click 未命中。`finally` 已 down。未跑 open。未跑 stop（栈已空）。
- 收口专属 C/V/N=0，控制目录已删除，共享 before/after 字节相同 C=15/V=9/N=1。证据 `/private/tmp/anhuan-material-rag-j6-clear-20260818` detached_root=`47e968d32defc0e53c418a9ad137764b9fb178418eba6a6c5099a73247b20f12`。旧证据只读。

## 2026-08-18 22:07｜J6 同页清空｜STARTED（已被 22:45 收口）

- 目标：同一真实 document 先非空 ready（答案节点在、citation row≥1），再 fail.clear；同 Network requestId 绑定 503/unavailable 后答案节点消失且 citation rows=0。成功才恢复机器门两标签；保持 `HUMAN_UAT_NOT_READY`。不 commit。Ark 外发=0。live≤2。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。专属 C/V/N=0，无控制目录，无并发 UAT。共享 `anhuan-f1` C=15/V=9/N=1 exited。24 路径 mode/SHA 已登记。
- 当时现役改为 `TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。20:24 的 `UAT_MACHINE_GATE_PASSED` 视为历史假绿（J6 初态已空仍写 `cleared_on_failure: true`）。
- 目标检查红：`Ran 58 / FAILED (failures=6)`。签名：`FRESH_EMPTY_FALSE_GREEN`、`ID_MISMATCH_FALSE_GREEN`、`J6_CLEAR_FN_MISSING`、`J6_SUMMARY_FN_MISSING`、localctl 无 `_validate_material_rag_uat_browser_summary`、`j6_prior_answer` 未入 browser_fn。
- 离线绿：同一命令 `Ran 58 tests in 3.034s / OK`，failures/errors/skipped=0。`git diff --check=0`。白名单外 SHA 漂移=0。建议：localctl J6 摘要测试放在允许的 `test_engineering_closeout_browser_runner.py`，因 `test_material_rag_uat.py` 不在白名单。
- Live 周期1：start 22:38:02→22:38:37 wall=34658ms exit 0，`HUMAN_UAT_URL http://127.0.0.1:57636/qa`。check 22:38:48→22:39:49 wall=60926ms exit 2。反向四门全 1。浏览器 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_SWITCH_FAILED`（无六键 JSON，非 J6 自有码）。check `finally` 已 down，专属 C/V/N=0。
- 一跳：J6 后收起 query Select；租户选项改为唯一可见 dropdown 外层 wrapper DOM `click()`，不再用全局 `clickElementWithText`。准备周期2。
- 顺序：反向测试先红（fresh empty / 同页 prior / requestId 错绑 / 五键写死缺失多余）→ 最小修 runner+localctl 判据 → 明卷≥53 绿 → live start/check，成功才 stop。
- 最大风险：两步之间 navigate 重挂载，或只查 Empty。禁止加时/retry/读正文。白名单外字节必须不变。

## 2026-08-18 20:24｜J6 AntD6 选择提交｜PASSED_MACHINE_GATE（历史；J6 清空假绿，已被 22:07 重开）

- 当时现役（历史，清空假绿已撤销）：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `HUMAN_UAT_READY`。未跑 `material-rag-uat-open`。未 commit。Ark 外发=0。本窗口 live 1/1 已用尽且通过。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。收口专属 C=0 V=0 N=0，控制目录已删除。共享 `anhuan-f1` C=15/V=9/N=1 全部 exited；start/check/stop 均 `shared_identity_unchanged=1`。
- 任务1–3 离线：只改 `selectClosedQuery`（可见唯一 enabled 外层 `.ant-select-item-option` 上 DOM `click()`，等展示值精确等于目标后再点检索）。`REQUEST_NOT_SENT` 拆为 `QUERY_NOT_COMMITTED/select`、`ASK_NOT_AVAILABLE/ask`、`POST_NOT_OBSERVED/observe_request`；六键 `journey,expected_phase,actual_phase,request_seen,http_status,action_stage`；localctl 缺键/多键拒绝。未改产品 UI/API，未改全局 `clickElementWithText`，未加 timeout/retry。
- 目标检查红→绿：先红 `Ran 53 / FAILED (failures=12, errors=1)`；同一命令后绿 `Ran 53 tests in 2.822s / OK`，failures/errors/skipped=0。
- 唯一 fresh live：start 20:21:09→20:21:45 wall=36060ms exit 0，`HUMAN_UAT_URL http://127.0.0.1:61181/qa`。重叠二次 start 被锁拒绝 `LOCAL_MATERIAL_RAG_UAT_ALREADY_RUNNING`，不计入新周期。check 20:23:41→20:23:50 wall=9123ms exit 0。stop 20:24:10→20:24:43 wall=33644ms exit 0。
- Live 摘要：`LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`；`journeys_passed=6`；`valid_tenant_count=2`；`cross_tenant_citation_denied=2` / `cross_tenant_delete_isolated=1` / `cross_tenant_state_isolated=1`；`unavailable_503=1`；唯一尾码 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。成功路径不打印六键 JSON；J6 合同为 POST 后 `request_seen=1` 再校验 HTTP 503 与 phase `unavailable`，否则不能写 `unavailable_503=1`。
- 建议替换：FakePage 初始值必须是 `provider.shared`，仅可见唯一 enabled exact wrapper 的 DOM `click()` 才提交 `fail.clear`；`clickElementWithText` 在 query 门抛错。证据目录 `/private/tmp/anhuan-material-rag-j6-select-20260818/cycle1`（0700/0600），detached_root=`2df34a471482e8c2a1803feb8594202f76cb5ed9a9f256947435c3f4454e05a5`。旧 `journey-gate-20260818` 只读。

## 2026-08-18 20:07｜J6 AntD6 选择提交｜STARTED（已被 20:24 收口）

- 目标：只修 J6 在 Ant Design 6 中选择未真实提交；拆 `select/ask/observe_request` 三码；唯一 1 次 fresh live 裁决机器门。成功仍保持 `HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING`。不 commit。Ark 外发=0。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。专属 C/V/N=0，无控制目录，无并发 UAT。旧两窗口 live 各 2/2 永久封存。
- 当时现役（已被 20:24 覆盖）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。不改产品 UI/API。不改全局 `clickElementWithText`。
- 末次旧证：`REQUEST_NOT_SENT` / `J6_FAIL_CLEAR` / `request_seen=0`（假说：option 未提交，尚未按阶段拆码）。锁定 antd 6.5.4、`@rc-component/select` 1.8.2。
- 顺序：红灯伪 Page（可见唯一 enabled wrapper / hidden|dup|disabled 拒绝 / 三阶段码 / J6 POST+503+unavailable）→ runner+localctl 六键证据 → 明卷≥51 红→绿 → 唯一 live start/check，成功才 stop。
- 最大风险：已选值被当成点击已验证，或点到 hidden/stale portal。禁止加时/retry/构造 POST。失败带更精确码即停。

## 2026-08-18 19:43｜旅程终态证据链｜STOPPED_LIVE_BUDGET（历史，旧窗口 live 2/2 已封存）


- 当时现役（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。未恢复机器门三个通过标签。未写 `HUMAN_UAT_READY`。未 commit。Ark 外发=0。本窗口 live 周期 2/2 用尽，禁止第三次。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。收口专属 C=0 V=0 N=0，控制目录已删除。共享 `anhuan-f1` C=15/V=9/N=1 全部 exited；未启停共享栈。
- 任务1–2 离线：先红 `Ran 47`（当时 runner 尚未 export 旅程门）后绿 `Ran 47 / OK`；周期1 live 后再补 antd6/证据测试，先红 `Ran 48 / FAILED (failures=3)`（`antd6-content`=`REQUEST_NOT_SENT`，uncommitted/disabled=`EVIDENCE_INVALID`），一跳后同一命令 `Ran 48 tests in 2.662s / OK`，failures/errors/skipped=0。
- 一跳（仅 runner）：antd 6 选中展示在 `.ant-select-content`（不再有 `.ant-select-selection-item`）；`REQUEST_NOT_SENT` 必须带五键证据。未改产品 UI，未加 timeout/retry。
- 本窗口 Live 周期1：`material-rag-uat-start` exit 0，wall=35038ms，`HUMAN_UAT_URL http://127.0.0.1:54291/qa`。check exit 2，wall=58392ms；反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`；浏览器 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED REQUEST_NOT_SENT`（当时无旅程 JSON）。`finally` 已 down。
- 本窗口 Live 周期2：start exit 0，wall=35071ms，`HUMAN_UAT_URL http://127.0.0.1:56113/qa`。check exit 2，wall=61236ms；反向门同上；浏览器 `REQUEST_NOT_SENT {"actual_phase":null,"expected_phase":"unavailable","http_status":null,"journey":"J6_FAIL_CLEAR","request_seen":0}`。能跑到 J6 表示 J1–J4 UI 与中间 HTTP 隔离段已过。`finally` 已 down。未跑 open，未跑第三次 live。
- 证据目录（0700/0600）：`/private/tmp/anhuan-material-rag-journey-gate-20260818/cycle1|cycle2`。旧窗口 18:53 的 `UAT_PHASE_MISSING` 与本窗口码不同，不得合并计数。
- 下一跳（未执行）：J1–J4 的 `?query=` 已预选目标，option 点击即使无效也能过选中等待；J6 从 `/qa` 默认 `provider.shared` 必须改成「失败并清空旧结果」。应对 `.ant-select-item-option-content`/title 做一次 DOM `click()` 再等展示值。禁止加时。禁止本窗口再 live。

## 2026-08-18 19:07｜旅程终态证据链｜STARTED

- 目标：修 browser runner 导航/选中/请求绑定/错误分类，live 取得旅程级证据后最多一跳复验。成功只恢复机器门；保持 `HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING`。不 commit。Ark 外发=0。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。专属 C/V/N=0，无控制目录，无并发 UAT。旧窗口 live 2/2 已封存；本窗口新授权 live≤2。
- 当时现役（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。冻结双租户映射、CRM 闭集、compose 标签。不先改产品 UI。
- 顺序：伪 Page 红灯→runner 绑定 POST/终态/六旅程码→离线≥41 红→绿→live 周期1；失败才按唯一证据一跳后周期2。
- 最大风险：`waitForPhase` 仍被旧 `/qa` 或初始 empty 假绿，或 header 被 `uatPost` 污染。禁止加时/重试掩盖竞态。

## 2026-08-18 18:53｜双租户隔离+资源门｜STOPPED_LIVE_BUDGET

- 当时现役（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。未恢复三个 UAT 通过标签。未 commit。Ark 外发=0。live 周期 2/2 用尽。
- 任务0核：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24，staged=0，`git diff --check=0`。收口时专属 C=0 V=0 N=0，控制目录 `/private/tmp/anhuan-material-rag-uat-874c22204849` 已删除。共享 `anhuan-f1` 仍 C=15/V=9/N=1 全部 exited；未启停共享栈。
- 任务1–3 离线已绿：catalog 按认证 tenant 原样映射 A/B；CRM 闭集名绑定；overlay `scope=material-rag-uat`；同名异主 `FOREIGN_RESOURCE` 且未删；check `finally` 清理；空 `command.lock` 允许删控制目录；start 打印 `HUMAN_UAT_URL` + `material-rag-uat-open`。
- 目标检查（本窗口末次）：`PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner` → 先红 `Ran 41 / FAILED (failures=8)`，后绿 `Ran 41 tests in 0.885s / OK`，failures/errors/skipped=0。覆盖双合法租户、资源身份拒绝、cleanup、人工交接。
- Live 周期1：`material-rag-uat-start` exit 0（`HUMAN_UAT_URL http://127.0.0.1:64405/qa`，`resource_identity_verified=1` `shared_identity_unchanged=1` `human_uat_url_ready=1`）。`material-rag-uat-check` exit 2，当时无浏览器失败码转印，`finally` 已 down。
- Live 周期2：同一 start 再绿；check 转印 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_PHASE_MISSING`；反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`。check `finally` 清空专属栈与控制目录。未跑第三次 live。未跑 `material-rag-uat-open`。
- `UAT_PHASE_MISSING` 来自 `waitForPhase`（J1 ready / J4 empty / J6 unavailable 共用此码）。61s 墙钟与「登录后第一次 20s phase 等待」相符，隔离 UI 切换未取证。下一窗先把 terminal phase 拆成固定码，禁止无新证据盲重跑 check。

## 2026-08-18 18:12｜双租户隔离+资源门｜STARTED

- 目标：真实 Keycloak 双合法租户 A/B 隔离 + 资源身份门 + 人工 /qa 交接。当时现役（历史）`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。不 commit。Ark 外发=0。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`，dirty=24（14M+10?），staged=0，`git diff --check=0`。专属 C/V/N=0。共享 `anhuan-f1` C=15/V=9/N=1 exited，canonical fingerprint=`e55770d2a87beda210c67762936085ea24b16df4ee460ae42c62f8d22cfae376`。
- 顺序：双租户红灯测试→最小映射/闭集 CRM→overlay+身份拒绝+失败清理→人工 URL/headed 登录→unittest≥36→live start/check/stop 各 1 次。
- 最大风险：catalog 仍把 B 映到 A，或 `down --volumes` 误删同名异主。额度 3h、目标检查≤8、live 周期≤2。

## 2026-08-18 17:34｜HUMAN_UAT_READY（历史，已撤销）

- 目标完成（历史，含 `HUMAN_UAT_READY`，已撤销）：真实浏览器→本地后端→Keycloak 租户鉴权。当时状态：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未签字，未 commit。
- 红→绿：鉴权接线测试先 4 fail + 7 err；修复后 `Ran 36 / OK`。默认 flag 关 404、无 token 401、错误角色 403、非成员/未知客户 404 均在 live reverse 变红后记 1。
- Live gate 1/2：`./scripts/localctl material-rag-uat-check` exit 0；六旅程 `journeys_passed=6`；尾码唯一 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`；`uat_actor_header_present=0`；`residual_count=0`；`cleared_on_failure=true`。随后 `material-rag-uat-stop` exit 0，专属 C/V/N=0，共享指纹未变。
- 默认路径仍关：host `npm run build` 无 Vite UAT=1；公共 `/material-qa` 自由提问 fail-closed。外发=0。未跑 161 / material-rag-verify。
- 建议替换：认证 tenant 一律映射合成 `ENTERPRISE_A` catalog；真实 CRM id 经 `get_account` 后按 id 排序映射 `CLIENT_A`/`CLIENT_B`。原因：本地 Keycloak 企业 UUID 不是合成 catalog 键。

## 2026-08-18 16:56｜MATERIAL_RAG_HUMAN_UAT_READINESS_STARTED


- 目标：把 OFFLINE_UI_STATE_GATE 升级为真实浏览器→本地后端→Keycloak 租户鉴权；只到 `HUMAN_UAT_READY`，不代替签字，不 commit。Ark 外发=0。
- 任务0：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，dirty=19（10M+9?），staged=0，`git diff --check=0`。19 路径 mode/size/SHA 已登记。
- 三首因：compose 未传 `F1_MATERIAL_RAG_UAT_LOCAL`；`web.Dockerfile` 无 Vite flag；后端要 `X-Uat-Actor` 而浏览器只发 Authorization/`X-Enterprise-Id`。`uatBrowserGate.mjs` 无网络。
- 顺序：鉴权接线红→绿 → 专属 overlay+localctl → CDP `/qa` 六旅程 → unittest/lint/build → live-check≤2 后 stop。
- 最大风险：把 UAT flag 漏进默认栈，或用 X-Uat-Actor/合成企业绕过 Keycloak。额度 60 分钟、改动批≤6、目标检查≤12、live gate≤2。

## 2026-08-18 14:54｜UAT 机器门收口

- 当时现役（历史）：`UAT_MACHINE_GATE_PASSED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `UAT_PASSED` / `RELEASE_VERIFIED` / production。未 commit/push。
- 专属 `tests.test_material_rag_uat`：exit 0，`Ran 21 / OK`。公共 `tests.test_material_rag -k test_public_`：exit 0，`Ran 4 / OK`。前端 `npm run lint` / `npm run build` 各一次 exit 0（沿用已有 sibling `node_modules`，本仓未 `npm install`）。`git diff --check=0`。本任务进程 0。
- 固定入口 `POST /api/v1/local-uat/material-qa` 仅 `F1_MATERIAL_RAG_UAT_LOCAL=1`。公共 `/material-qa` 自由提问仍 fail-closed。真实 Ark 检索腿仍 BLOCKED。
- 证据：[MATERIAL_RAG_UAT_REPORT.md](./MATERIAL_RAG_UAT_REPORT.md)


## 2026-08-18 14:34｜MATERIAL_RAG_UAT_STARTED

- 从 checkpoint `a72fdb186de2ab53f6c8d72983f1b24fc99dac1e` 开始离线产品 UAT 机器门。branch=`codex/material-rag-scanner-protocol`，工作树 clean。
- 最终只允许：`UAT_MACHINE_GATE_PASSED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。不得写 `UAT_PASSED` / `RELEASE_VERIFIED` / production。
- 公共 `/material-qa` 自由提问保持 fail-closed。真实 Ark 检索腿保持 BLOCKED，不得读/用旧 key。只用合成数据、固定 query_id、本地 deterministic adapter。
- 顺序：枚举调用链 → UAT 场景矩阵 → 红灯专属测试 → 最小前后端 → 绿灯与 lint/build → 报告。不 commit/push。不跑 161 项、不跑 material-rag-verify、不触碰共享 `anhuan-f1`。
- `ARK_KEY_ROTATION_REQUIRED`。UAT 改动保持未提交。

### 调用链取证（修改前）

- `/qa`：`src/web/src/pages/QAPage.tsx` 仅禁用告示，无表单、无 API。
- `/controlled-documents`：P3 `DocumentLibraryPage` / `DocumentDetailPage`；上传扫描预览释放已有。
- 客户详情：`CrmAccountDetailPage` + `ScopedMaterialUploadButton`（`kind=client`）。
- 公共 HTTP：`POST /api/v1/material-qa`（`question`+`request_id`+可选 `client_account_id`，`extra=forbid`）→ `derive_retrieval_context` → `qa_service.ask_material_question`。任意 question 在 DB/网络前完成 `MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED`，不调 RAGFlow/Ark。
- `run_verified_retrieval` 对任意 question 同样 fail-closed。无本地固定-query 路由。前端无 `features/material-rag/`。

### UAT 场景矩阵（合成 fixture，外发=0）

| ID | 角色/入口 | fixture | 动作 | 期望 UI | 期望 HTTP/码 | DB/网络副作用 | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| J1 | 服务商 /qa + 文档库 | provider 合成单元 | 上传→扫描/预览→释放→索引→`provider.shared`→引用跳转 | loading→ready；scope=服务商共享 | 200；引用仅 provider | 无外发；仅内存 catalog | UAT 测试 + browser gate |
| J2 | 客户详情→/qa?client=A | client A 单元 | `client.current` | scope=当前客户；无 provider 串入 | 200；仅 client A | 无外发 | 同上 |
| J3 | /qa 组合 | provider+A；B 空 | `combo.provider_client` 对 A 与 B | A=共享+A；B=仅共享，无 A 客户引用 | 200；B 不回退 A | 无外发 | 同上 |
| J4 | 跨客户/跨租户/未授权引用 | 企业B / 未知 client | `cross.denied` / 打开他户 citation | denied；无物理 ID | 404 `MATERIAL_CONTEXT_NOT_FOUND` / `MATERIAL_CITATION_NOT_FOUND` | 无外发 | JSON 键扫描 |
| J5 | 幂等/冲突/重建/删除 | 同 request_id | 重放；换客户；rebuild；delete | conflict / 残留 0 | 200 重放；409 `REQUEST_ID_CONFLICT`；delete `residual_count=0` | 无外发 | store 计数 |
| J6 | QA 页状态机 | 先成功再 `fail.clear` | loading/empty/disabled/in-progress/conflict/unavailable/retry/recovery | 失败后 answer/citations 清空 | 202 `REQUEST_IN_PROGRESS`；503 `MATERIAL_RAG_UNAVAILABLE` | 无外发 | journeyMachine + browser gate |

公共自由提问与未知 `query_id` 在网络/DB 前拒绝。固定入口仅 `F1_MATERIAL_RAG_UAT_LOCAL=1`。

## 2026-08-18 03:14｜合同漏项检查点｜开工回执

- 目标：修 `_closed_f1_migrate_target` 不可哈希泄漏与 wave2/3/4 仍断言 head=`f1_0014` 两处确定漏项；新建 20260818 可提交检查点。本轮不跑 Docker verify、不 commit。
- 任务0已核：cwd/branch/`272a987`、dirty=21、staged=0、untracked=0、`git diff --check=0`。`[]/{} /bytearray()` 现为 `TypeError`；wave2/3/4 `get_heads()==["f1_0014"]`。旧检查点 `.../material-rag-engineering-checkpoint-20260817` 永久只读（树根 `3aa66dec104b43cfe89fb766c0ba54234ec034d544860beca759fa57c278a860`，98 文件）。
- 顺序：type-is-str 闭集拒绝 → 扩非法对象测试 → wave2/3/4 脚本 head=`f1_0015` 且 `down_revision=f1_0014` → 聚焦 7 模块 unittest → 新检查点+隔离重建。
- 最大风险：把默认运行目标改回 `head`/`0015`，或改到 seed/verify/backup 的 0014/35 合同。
- 额度：2 小时、聚焦 unittest≤12 次、clean-clone≤3。现役只承认 `SMOKE_PASSED / CHECKPOINT_REPRODUCIBLE / NOT_PRODUCTION`，达标后再恢复 `DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY`。

## 2026-08-18 03:20｜任务1｜不可哈希 target 与 wave2/3/4 head

- `_closed_f1_migrate_target`：先 `type(target) is not str`，再闭集 `{f1_0014,f1_0015}`；一律 `RuntimeError("F1_MIGRATE_TARGET_INVALID")`。发生在 `driver_connection` / `command.upgrade` 之前。默认仍 `f1_0014`；专属 migrator 未改。
- 非法对象测试扩到 `[]`、`{}`、`set()`、`bytearray()`；`type(raised.exception) is RuntimeError`。原生 TypeError 不算通过。
- wave2/3/4：`get_heads()==["f1_0015"]` 且 `f1_0015.down_revision=="f1_0014"`；保留各波次 `f1_0005.down_revision=="f1_0004"`。未改 seed/verify/backup/atomicity 的 0014/35。

## 2026-08-18 03:21｜任务2｜聚焦验证

- 1/12 失败签名：`test_f1_migrate_target_is_closed_default_0014_not_head` 断言 `'type(target) is str'`，源码为 `type(target) is not str`；wave1/2/3/4/p3 `ModuleNotFoundError: platform_foundation`；material_rag `F1_KEYCLOAK_ISSUER_URL_REQUIRED`。exit 1，Ran 62，failures=1 errors=12。
- 改变：测试改为锁定 `type(target) is not str`。建议替换：字面命令未导出 `PYTHONPATH`；本 shell 无 `src`。沿用既有工程 runner `PYTHONPATH=$ROOT/src` 与既定 `F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan`。未放宽断言、未改有效 0014/0015 路径。
- 2/12：同一 7 模块命令，`Ran 161 tests in 0.413s` / `OK`，failures=errors=skipped=0，exit 0。`git diff --check` 为空。dirty=24。不重复跑。

## 2026-08-18 03:22｜任务3/4｜新检查点 20260818

- 旧检查点 `.../material-rag-engineering-checkpoint-20260817` 只读。新目录 `.../material-rag-engineering-checkpoint-20260818`。patch/root/包级总root 只引用外部 `RESULT.v2.txt`，本文不内嵌自指 root。
- 本轮未运行 Docker；沿用此前 clean-clone `523069.978ms / LOCAL_MATERIAL_RAG_VERIFY_OK`，不是本轮重跑。
- 状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY / NOT_PRODUCTION`。`ARK_KEY_ROTATION_REQUIRED`。不是 `RELEASE_VERIFIED`。不 commit/push。

## 2026-08-18 00:05｜双迁移检查点｜开工回执

- 目标：把已 smoke 的 material-RAG 固化为可逐字节重建检查点；默认工程精确停 `f1_0014/35`，专属精确用 `f1_0015/38`；里程碑 `DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY`。不 commit/push。
- 任务0已核：branch/HEAD `272a987`、dirty=15、staged=0、untracked=0、+/- 7754/566、`git diff --check=0`、无并发 verify、C/V/N=0、共享 15 exited、v3 根锚 `0f5ecc00…` 合同。编辑前检查点 `/Users/lichenhao/Desktop/安环项目/artifacts/material-rag-engineering-checkpoint-20260817`（0700/0600），pre root `e87e672c319d59f1b3abd75275ed846ffafc963661f7e550d09a9b92826350c9`，patch `dffd999fe035f0c9605f9919bc440d04873bf1af9666fad3c3def266bcf01d65`。
- 顺序：锁死闭集 target（红→绿）→ 合并回归 → bundle+final patch 隔离 clone 对账 → clone 内最多 3 次 verify。
- 最大风险：把 Alembic 文件 head 误当成默认运行目标，或机械把 seed/verify/backup 改成 0015。
- 额度：8 小时、clone 内最多 3 次完整 verify。保留 15 dirty。不改 f1_0015 SQL/RLS、业务逻辑、Compose。

## 2026-08-18 00:12｜任务1 红→绿｜双迁移闭集 target

- 红：`test_f1_migrate_target_is_closed_default_0014_not_head` `AssertionError: None != 'F1_DEFAULT_MIGRATE_TARGET'`；`test_dedicated_migrate_requests_closed_f1_0015_not_head` 缺 `F1_MATERIAL_RAG_MIGRATE_TARGET`。证明默认调用仍走 `head`。
- 绿：同一两测试 + `test_f1_role_schema_upgrade_and_owner_finalize_stay_ordered` + P2/P3 linear-head 共 5 项 `Ran 5 / OK`。
- 实现：`migrate_with_connection` 内部闭集 `{f1_0014,f1_0015}`，默认 `f1_0014`；`material-rag/migrate.py` 显式 `target=F1_MATERIAL_RAG_MIGRATE_TARGET`（0015）。非法值 `F1_MIGRATE_TARGET_INVALID`。`main`/`local_migrate` 不传 target。P2/P3 的 `get_heads()` 改为承认脚本 head=`f1_0015`，同时锁定默认运行目标仍为 `f1_0014`；未改 seed/verify/backup 的 0014/35 合同。

## 2026-08-18 00:16｜任务2｜合并范围回归通过

- `/Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest tests.test_engineering_closeout_migration tests.test_engineering_closeout_atomicity tests.test_engineering_closeout_verify tests.test_engineering_closeout_backup tests.test_p2_wave1 tests.test_p3_controlled_ingestion tests.test_material_rag`
- `Ran 164 tests in 1.667s` / `OK`，failures=errors=skipped=0，exit 0。
- 中间一次失败：`test_verifier_failure_reason_is_explicitly_allowlisted` 未计入 dirty localctl 已有的 `material-verifier` 键；补进期望闭集后重跑全绿。未改成功判据、未 skip。
- `git diff --check=0`。tracked dirty=21（原 15 + 白名单 6），staged=0，untracked=0。
- 当时误标 `CHECKPOINT_READY`；真正的 clean-clone verify 见任务3。

## 2026-08-18 02:20｜任务3｜clean clone 重建与 verify

- 编辑后检查点 `artifacts/material-rag-engineering-checkpoint-20260817/post/`。最终 patch/root 以该目录文件为准，本文不内嵌自指 root。
- clone verify 1/3（`/tmp/anhuan-material-rag-checkpoint-clone-vU7Xhqy6`）：exit 2，墙钟 `1074908.427ms`，stdout 空，`IMAGE_SOURCE=MATCH` root `b2e45cded0201db14b2b2bb7fae0baf3ea9f5f40820651d49eeb5dd23393b08c`，`LOCAL_MATERIAL_RAG_P3_UPLOAD_HTTP_FAILED`。CRM 201 已过；专属其余服务当时 healthy；cleanup 后 C/V/N=0，共享 15 exited UNCHANGED。保全 `clone-verify-1/`。未改 verifier/P3/Compose。
- 重建 clone 后任务2 出现 `test_browser_stage_summaries_require_exact_stage_tags` 红灯：`wrong_stage` 取自 `BROWSER_STAGES` 无序遍历，`PYTHONHASHSEED` 有时抽到 `pwa-os`（不在 `BROWSER_STAGE_TAGS`）得到 `LOCAL_BROWSER_STAGE_INVALID`。白名单内把对照 stage 改为 `sorted(BROWSER_STAGE_TAGS)`，并锁定 `pwa-os` → `STAGE_INVALID`。HASHSEED 0/1/2/random 单测全绿；主仓与新 clone `Ran 164 / OK`。未改 localctl。
- clone verify 2/3（`/tmp/anhuan-material-rag-checkpoint-clone-XRknJK`）：`./scripts/localctl material-rag-verify` exit 0，墙钟 `517365.713ms`，stdout 恰 2 行 canonical metrics JSON + `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH` 同上 root，C/V/N=0，共享 UNCHANGED。保全将写入 `clone-verify/`（本段冻结后的最终 clone 再跑 3/3）。
- 状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY / NOT_PRODUCTION`。`ARK_KEY_ROTATION_REQUIRED`。不是 `RELEASE_VERIFIED`。不 commit/push。

## 2026-08-17 22:38｜parser 收紧窗口｜VERIFY_OK 收口

- 从冲突窗 v10 继续，不重做历史窗口。cwd/branch/HEAD `272a987`；dirty=15 全保留；`git diff --check=0`；无并发 verify；专属 C/V/N=0；共享 15 exited UNCHANGED；Ark lstat 合同（regular/非链接/nlink=1/0600/uid=euid=501/size=46）。未扩 GRANT。不 commit/push。
- 本窗口完整 verify 3/40：v1 `OUTPUT_INVALID` `COMPOSE_ATTACH|JSON|VERIFY_OK|OTHER` envelope_count=1 → v2 同签名且 `other_kind=EXITED_CODE` → v3 exit 0、`IMAGE_SOURCE=MATCH`、stdout 恰为 canonical JSON + `LOCAL_MATERIAL_RAG_VERIFY_OK`。
- 根因：Compose v5.3.1 在 JSON+OK 外夹 `Attaching to …` 与 bare `material-rag-verifier-1 exited with code 0`（副本后缀，旧 bare 只允许无 `-1`）。pair 外只收严格 `COMPOSE_*`；额外 JSON/VERIFY_OK/OTHER 拒绝；`Exited (0)` 尾部有界（含 `Less than a second ago`）。
- 聚焦检查：`test_metrics_are_integer_only_and_cover_negative_gates` 每次改 parser 后 `Ran 1 / OK`。回归 `tests.test_material_rag` `Ran 45 tests in 1.185s / OK`。回归未改代码，未再跑 verify。
- v3 保全 `/private/tmp/anhuan-material-rag-longrun-20260817/v3` 根锚 `0f5ecc0091ba47d45263eea962a8b6ac52d36771530ab4b2366ca2f9459a5b7c`，墙钟 `536876.190ms`。IMAGE_SOURCE root `b2e45cded0201db14b2b2bb7fae0baf3ea9f5f40820651d49eeb5dd23393b08c` MATCH。
- 状态：`TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION`。不是 `RELEASE_VERIFIED`，未部署。

## 2026-08-17 20:00｜冲突探针窗口｜历史（verify 10/10 额度耗尽，已被本窗收口）

- 任务0/1 已过：cwd/branch/HEAD `272a987`；IMAGE_SOURCE 每轮 MATCH；冲突探针红→绿 `test_scope_unit_db_snapshot_uses_working_api_select_session` `Ran 1 / OK`。未扩 GRANT。旧 autonomy v1–v3 未覆盖。
- 本窗口 verify 10/10，成功条件未达到，不记 `VERIFY_OK`/`SMOKE_PASSED`。专属 C/V/N=0，共享 15 exited UNCHANGED，Ark lstat 合同。
- 因果推进：v1 `UNIT_TESTS_FAILED`（探针拆行）→ v2–v4 `RETRIEVAL_FAILED`（NO_HITS，adapter 字段后过检索）→ v5–v6 `REBUILD_FAILED` 无分关 → v7 `REBUILD_FAILED` `REMOTE_SNAPSHOT`（job 已成功；默认 snapshot 在合成域之后仍要求只剩 client_a dataset）→ v8–v10 verifier 已打出 JSON+`VERIFY_OK`，localctl `OUTPUT_INVALID`。
- v10 完整输出证据：`LOCAL_MATERIAL_RAG_OUTPUT_EVIDENCE {"envelope_count":0,"line_classes":"OTHER|JSON|VERIFY_OK|OTHER","line_count":4,"mismatch":"LINE_COUNT","mismatch_key":"NONE"}`。保全 `/private/tmp/anhuan-material-rag-conflict-20260817/v10` 根锚 `8eedddbe7b6b94e7fd9ece338351d7b70717339f9da02b75239bf861d6accf14`，墙钟 `565006.715ms` exit 2。
- 已落地、额度内无法再跑 verify：rebuild/delete snapshot 传 `knowledge_scope_id=setup.client_a_scope_id`；localctl 从 stdout 抽取唯一 JSON+`VERIFY_OK` 对，忽略 Compose 信封（含小写 `container … exited with code 0`、`Attaching to`、`Exited (0)`）并去掉 ANSI。下一窗先跑一次 `./scripts/localctl material-rag-verify`，不要重做冲突探针。
- `ARK_KEY_ROTATION_REQUIRED`。不 commit/push。

## 2026-08-17 15:30｜任务1 红→绿｜冲突探针身份

- 红：`test_scope_unit_db_snapshot_uses_working_api_select_session` `Ran 1 / FAILED`（缺 operation_token reset）。
- 绿：同一方法 `Ran 1 test in 0.001s` / `OK`，failures=errors=skipped=0。`git diff --check=0`。
- 实现：snapshot 保存 ContextVar token 并 finally reset；冲突探针改 live worker claim（lock+fence+claimed_session，`PERSIST_UNITS`）；首个 replay 显式 claim 上探针回滚后再 `process_claimed_demo_job`。localctl 在 build 后、secret-init 前做 IMAGE_SOURCE 对账。未扩 GRANT。开始 verify 1/10。

## 2026-08-17 15:01｜冲突探针身份｜开工回执

- 目标：证伪/证实粘性 `DB_SNAPSHOT_EXIT` + `f1_api` 写 unit；修冲突探针到 live worker claim；IMAGE_SOURCE 对账后连续 verify 直到 `LOCAL_MATERIAL_RAG_VERIFY_OK`。旧窗 autonomy v1–v3 不覆盖、不重跑。
- 任务0：cwd/branch/HEAD `272a987` 合同；dirty=15；`git diff --check=0`；无并发 verify；Docker 可用；专属 C/V/N=0；共享 15 exited；Ark lstat 合同。PROJECT_STATUS L65 仍写 CHUNK_ADD 现役，收口时改。
- 顺序：IMAGE_SOURCE 闭集对账（build 后、secret-init 前）→ 扩展现有 snapshot 测试红→绿（token reset、PERSIST_UNITS、禁止 f1_api 写 unit、同一 claim）→ 最多 10 次 verify。
- 额度：180 分钟、10 次 verify、10 批修改。冻结 proxy/Dockerfile/Compose/P3/fixture。不 commit/push。成功一次即停。不扩 GRANT。

## 2026-08-17 14:40｜verify 3/10｜同一 EXIT/42501 第三次，停止

- 保全 `/private/tmp/anhuan-material-rag-autonomy-20260817/v3`。墙钟 `365502.994ms` exit 2。根锚 `091710cf88711a0f3a36ff12a11ecc0dd7f21bb0a87cf1d5c7c2773e71799ab2`。共享 UNCHANGED。C/V/N=0。
- v1=v2=v3 证据字节相同：INTERNAL `DB_SNAPSHOT_EXIT` / `PJ_INDEX_REPLAY` / 42501。两次不同修复后第三次仍相同，按任务书停止，不跑 verify 4。
- 未扩 GRANT。不能记 `VERIFY_OK`/`SMOKE_PASSED`。本窗口 verify 3/10。
- 状态保持 `TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。`ARK_KEY_ROTATION_REQUIRED`。

## 2026-08-17 14:32｜verify 2/10｜同一 EXIT/42501，第二次不同修复

- 保全 `/private/tmp/anhuan-material-rag-autonomy-20260817/v2`。墙钟 `355915.793ms` exit 2。根锚 `1b3d3e21ae731c3f70d94bd8a6efec734224dc37863cc8c2dc1ee6567d5f91ec`。共享 UNCHANGED。C/V/N=0。
- 签名与 v1 相同：INTERNAL `DB_SNAPSHOT_EXIT` / `PJ_INDEX_REPLAY` / 42501。证据字节与 v1 相同。每 version 一会话未改变该签名。
- 本批第二次不同修复：LOAD 会话内显式 `rollback`（避免 aexit 二次 rollback）；unwrap 后分类 DB 错落到 SNAPSHOT_* INDEX checkpoint。红→绿同一测试 `Ran 1 / OK`。开始 verify 3/10。若仍完全相同则停。

## 2026-08-17 14:24｜verify 1/10｜LOAD 已过，42501 在 session EXIT

- 保全 `/private/tmp/anhuan-material-rag-autonomy-20260817/v1`。墙钟 `368003.943ms` exit 2。根锚 `75a237ed0a5a32c12815193fd30b124792a46e26ac9320dfbb0e782aa79bd0f3`。共享 UNCHANGED。C/V/N=0。
- 新签名（不计入旧窗三次止损）：INTERNAL `DB_PROGRAMMING` / `DB_SNAPSHOT_EXIT` / `PJ_INDEX_REPLAY` / 42501。known-version LOAD 已成功；失败在长会话退出。scope 级 SELECT 不是本轮充分因。
- 本批：每 known version 独立 `session_scope`（对齐 `_load_version_units`）；DB 错经 `_classify_db_error` 落到 SNAPSHOT_* checkpoint。红→绿同一 snapshot 测试 `Ran 1 / OK`。开始 verify 2/10。

## 2026-08-17 14:16｜任务1 红→绿｜known-version snapshot

- 红：`test_scope_unit_db_snapshot_uses_working_api_select_session` `Ran 1 / FAILED`（签名无 `version_ids`）。
- 绿：同一方法 `Ran 1 test in 0.000s` / `OK`，failures=errors=skipped=0。`git diff --check=0`。
- 实现：snapshot 只收已知 `version_ids`，`tuple(sorted(set(version_ids)))` 后逐个 `load_units_for_version`。replay 前后与 conflict 传 `tuple(sorted(persisted_by_version))`。保留 `_unit_counts` 全 scope 总数。operation/checkpoint 拆 OPEN/LOAD/EXIT。未改 RLS/GRANT。scope SELECT 仍为待证假设。开始 verify 1/10。

## 2026-08-17 14:10｜known-version snapshot｜开工回执

- 目标：known-version 查询收 snapshot 盲区，补 OPEN/LOAD/EXIT 固定枚举；再最多 10 次 verify 打通全闭环，取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`。旧窗 5 次不计入。
- 任务0：cwd/branch/HEAD `272a987` 合同；dirty=15；`git diff --check=0`；无并发 verify；Docker 可用；专属 C/V/N=0；共享 15 exited；Ark lstat 合同。不重做 v1–v5。
- 现役签名仍是 INTERNAL `DB_SNAPSHOT`/`PJ_INDEX_REPLAY`/42501。scope SELECT 仅为待证假设。PROJECT_STATUS 已是该签名，不是 CHUNK_ADD 现役。
- 顺序：任务1 扩展现有 snapshot 测试红→绿（version_ids、禁止 scope SELECT、保留 `_unit_counts`）→ 任务2 连续 verify。
- 额度：180 分钟、10 次 verify、10 批修改。冻结 proxy/Dockerfile/Compose/P3/fixture。不 commit/push。成功一次即停。

## 2026-08-17 04:53｜verify 5/10｜同一 42501 签名第三次，停止

- 保全 `/private/tmp/anhuan-material-rag-egress-20260817/v5`。墙钟 `380014.485ms` exit 2。根锚 `d427ae732994eea0f55c05aa51d74e69a2eab219c3dd0ef48db5805f1446cc24`。共享 UNCHANGED。C/V/N=0。stdout 空。verifier `exited/1`，`oom_killed=false`。
- 完整签名第三次相同：INTERNAL `DB_PROGRAMMING` / `DB_SNAPSHOT` / `PJ_INDEX_REPLAY` / sqlstate `42501`。已用两次不同修复（ADMIN_SUB+去 created_at；DISTINCT version + `load_units_for_version`）。按任务书停止，不跑 verify 6。
- 未扩 GRANT/BYPASSRLS。PRIMARY_INDEX + PRIMARY_ATTEST 仍过。不能记 `VERIFY_OK`/`SMOKE_PASSED`。
- 本窗口 verify 5/10。状态保持 `TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-17 04:46｜verify 4/10 后第二次不同修复｜snapshot 改走 load_units

- v4 保全 `/private/tmp/anhuan-material-rag-egress-20260817/v4`。墙钟 `388725.097ms` exit 2。根锚 `a4394369de4c0c6ff75e36dfefba92cc077a1d8dbaa2c98b69593d103dd16522`。共享 UNCHANGED。C/V/N=0。
- 同一完整签名第二次：INTERNAL `DB_PROGRAMMING` / `DB_SNAPSHOT` / `PJ_INDEX_REPLAY` / sqlstate `42501`。ADMIN_SUB + 去 `created_at` 已证伪。未扩 GRANT。
- 本批第二次不同修复：`_scope_unit_db_snapshot` 只 `SELECT DISTINCT document_version_id`，再复用已证 PRIMARY_INDEX 的 `load_units_for_version` 取指纹。不 SELECT 宽列/密文。
- 红：`test_scope_unit_db_snapshot_uses_working_api_select_session` 缺 `load_units_for_version`。绿：同一方法 `Ran 1 / OK`。`git diff --check=0`。开始 verify 5/10。若仍完全相同签名则停止。

## 2026-08-17 03:18｜egress 分流｜开工回执

- 目标：INDEX 失败后转印唯一 canonical egress 计数；再最多 10 次 verify 打通四份 Demo 全闭环，取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`。
- 顺序：任务0核对照 → 任务1红→绿 egress 转印 → 任务2按计数自治修复（unauthorized/json/4xx/全0/本地）→ 成功即停。
- 任务0：cwd/branch/HEAD `272a987` 合同；dirty=14；`git diff --check=0`；无并发 verify；Docker 可用；专属 C/V/N=0；共享 15 exited；Ark lstat 合同；v5 根锚与 `CHUNK_ADD_CODE_400` 吻合。不重做 v1–v5。
- 最大风险：把正文/hash/URL 打进 egress 证据；用通配放宽 relay；把 400 预设成 Ark 或 allowlist。
- 额度：180 分钟、最多 10 次 `./scripts/localctl material-rag-verify`、10 批修复。不 commit/push。成功一次即停。

## 2026-08-17 03:20｜任务1 红→绿｜INDEX 失败转印 egress

- 红灯：`test_index_failure_transfers_egress_audit_without_text` `Ran 1 / FAILED`（`_EGRESS_EVIDENCE.clear()` 不存在）。
- 绿灯：同一方法 `Ran 1 / OK`，errors=failures=skipped=0。`git diff --check=0`。
- 实现：`main()` 离开 stderr redirect 后 `_EGRESS_EVIDENCE.emit_for_reason`；lenient 快照只含固定计数+`audit_status`；localctl INDEX_FAILED 唯一转印，DUPLICATE/MISSING/MALFORMED 降级。禁止正文/hash/URL/响应体。

## 2026-08-17 04:34｜verify 3/10｜attest 已过，replay 快照 42501

- 保全 `/private/tmp/anhuan-material-rag-egress-20260817/v3`。墙钟 `369658.613ms` exit 2。根锚 `0373da53cb086584ba64134d57938cb86068f32554723cb1fd2ee1c84eed85ac`。共享 UNCHANGED。C/V/N=0。
- get_chunk 字段修复已过：阶段 `PJ_INDEX_REPLAY`。INTERNAL `DB_PROGRAMMING` / `DB_SNAPSHOT` / sqlstate `42501`。
- `_scope_unit_db_snapshot` 用 `EMPLOYEE_SUB` 且多选 `created_at`；已证工作的 `_load_version_units` 用 `ADMIN_SUB` 且含 `body_ciphertext`。未扩 GRANT。
- 本批：快照改为 `ADMIN_SUB` + 与 load_units 同列（去掉 `created_at`）+ `enterprise_id` 谓词。红→绿 `test_scope_unit_db_snapshot_uses_working_api_select_session` `Ran 1 / OK`。开始 verify 4/10。

## 2026-08-17 04:24｜verify 2/10｜Ark 2xx=286，attest 卡在 get_chunk 字段

- 保全 `/private/tmp/anhuan-material-rag-egress-20260817/v2`。墙钟 `345699.129ms` exit 2。根锚 `8a2dc87353963f7c77789fe857bab1ad13fef71884091d9c331ba68152959127`。共享 UNCHANGED。C/V/N=0。
- INDEX `REMOTE_SNAPSHOT` / `PJ_PRIMARY_ATTEST`。EGRESS authorized=forwarded=upstream_2xx=286，rejected_*=0。fake-ip 修复已证伪为成功：chunk add 已通。
- 钉住 RAGFlow v0.26.4：`list_chunks` 把 `content_with_weight` 映射为 `content`；`get_chunk` 返回 ES 原文。verifier 读 `content` 为空后 `_fail_fixed(REMOTE_SNAPSHOT)`。
- 本批：`_chunk_detail_content` / `_chunk_detail_tags` 兼容 pinned get_chunk。红→绿 `test_remote_snapshot_reads_pinned_get_chunk_content_field` `Ran 1 / OK`。开始 verify 3/10。

## 2026-08-17 04:08｜verify 1/10｜egress READY 且 upstream 全 0

- 保全 `/private/tmp/anhuan-material-rag-egress-20260817/v1`。墙钟 `196860.646ms` exit 2。根锚 `95ecfa2b29dbf9d99a876c888f0f901182423b6bbeed8762551a0f6e05cdf918`。共享 UNCHANGED。C/V/N=0。
- INDEX 仍 `CHUNK_ADD_CODE_400`。EGRESS `READY`：authorized=1 forwarded=1，rejected_*=0，upstream_2xx/4xx/5xx=0。不是 allowlist/json/model/path。
- 固定 canary（系统 TLS，不打印 key/正文）：`STATUS_CLASS=HTTP_200`。venv/certifi 对同一主机 `CERT_VERIFY`。Docker 解析为 `198.18.0.0/15` 且容器内 TLS=OK。
- 根因：relay `_public_addresses` 只收 `is_global`，把 Clash fake-ip 丢掉后 `ARK_EGRESS_DNS_REJECTED`。RFC1918/loopback/link-local 仍拒绝。
- 本批：`_allowed_upstream_ip` 允许 `198.18.0.0/15` 作为 global 之后的回退。红→绿 `test_ark_relay_allows_benchmark_fake_ip_without_opening_rfc1918` `Ran 1 / OK`。开始 verify 2/10。

## 2026-08-17 02:48｜finish RLS｜v5 CHUNK_ADD_CODE_400 止损（5/6）

- 本窗口 verify 5/6：墙钟 `198068.002ms` exit 2。保全 `/private/tmp/anhuan-material-rag-finish-rls-20260817/v5`。根锚 `7be267bf6dc7a30004cb3edc1cc73f25f1218cf3831a05e39de09829cf4b9314`。共享 UNCHANGED。C/V/N=0。
- 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`。INDEX `reason_token=CHUNK_ADD_CODE_400`（HTTP 200，RAGFlow 业务码 400）。verifier `exited/1`，`oom_killed=false`。
- `"0"` 假说已证伪。未跑 v6：剩余 1 次不够「修后再现 2 次」；下一跳是 allowlist/egress，本窗口禁止改 fixture/allowlist 与 Ark endpoint/model。
- 目标测试 `test_material_rag_finish_policy_allows_terminal_without_broadening` 累计 7 次（任务1 红/绿、围栏 红/绿、拆枚举绿、probe-status 绿、chunk wrapper 绿），不得把测试绿写成 smoke。scanner allowlist 测试本窗口另 3 次全 OK。
- `ARK_KEY_ROTATION_REQUIRED`。不 commit/push。状态保持 `TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-17 02:40｜finish RLS｜v4 CHUNK_ADD_FAILED_200 + 接受 code \"0\"

- 本窗口 verify 4/6：墙钟 `201804.179ms` exit 2。保全 `/private/tmp/anhuan-material-rag-finish-rls-20260817/v4`。根锚 `0ffaf96979ea503c064f9fe121f97caa19179b7a3f8006d64147537873818ca5`。共享 UNCHANGED。C/V/N=0。
- 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`。INDEX `reason_token=CHUNK_ADD_FAILED_200`（HTTP 200，client 因 `code != 0` 抛 probe）。非 401/403 HTTP。verifier `exited/1`，`oom_killed=false`。
- 本批：adapter `_add_chunk_closed` 接受 HTTP 200 且 code 为 0 或 `"0"`；其余固定 `CHUNK_ADD_CODE_*`。不改 f0j1 client，不加 retry，不扩 GRANT。
- 目标测试累计 7 次（本批再 `Ran 1 / OK`）。scanner allowlist `Ran 1 / OK`。`git diff --check=0`。开始 verify 5/6。

## 2026-08-17 02:33｜finish RLS｜v3 PROBE_FAILED + 探针 reason/status 枚举

- 本窗口 verify 3/6：墙钟 `202815.826ms` exit 2，stdout 空。保全 `/private/tmp/anhuan-material-rag-finish-rls-20260817/v3`。根锚 `1a64fb3ba7c2ce2584c4e57ed304047d3f1f0d0dc6e4fb9060f56de55dcc9b86`。共享 UNCHANGED。专属 C/V/N=0。
- 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`。INDEX `PRIMARY_PROCESS`/`retry_wait`/`CLAIMED_SESSION`/`FINISH_TRUE`/`reason_token=MATERIAL_RAG_PROBE_FAILED`。verifier `exited/1`，`oom_killed=false`。
- 已证伪：v2 桶里的 ProvisionError 与 OSError。现役是 `RagFlowProbeError`。`CLAIMED_SESSION` 仍为残留。未输出异常正文/SQL/URL。
- 本批只加固定枚举：finish reason 改为 `DOC_LIST_FAILED_200` 这类 `reason_status`（status 仅 200/4xx/5xx 白名单）。401/403 则立即停。
- 目标测试再跑 `Ran 1 / OK`（累计 6 次）。scanner allowlist 测试 `Ran 1 / OK`。`git diff --check=0`。开始 verify 4/6。

## 2026-08-17 02:25｜finish RLS｜v2 新因果 + 拆 UNAVAILABLE 枚举

- 本窗口 verify 2/6：墙钟 `205647.793ms` exit 2，stdout 空。保全 `/private/tmp/anhuan-material-rag-finish-rls-20260817/v2`。根锚 `76fbbc49eaf804eae8ccfcbbd054743fb23f6b9e16963deee43ca012cc03ff73`。共享 before=after。专属 C/V/N=0。
- 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`。INDEX `PRIMARY_PROCESS`/`retry_wait`/`CLAIMED_SESSION`/`FINISH_TRUE`/`finish_sqlstate=NONE`/`reason_token=MATERIAL_RAG_UNAVAILABLE`。verifier `exited/1`，`oom_killed=false`；OCR 在 FAILURE_EVIDENCE 为 running/healthy。启动中曾见 ocr Exited 137，快照时已恢复，未改 Docker 内存。
- v1 的 `MATERIAL_VERSION_NOT_INDEXABLE` 未复现：lease-scoped `upload_task` UPDATE policy 已把围栏推进到 finish=`retry_wait`。不是同一因果签名。
- 本批只加固定枚举：把 `RagFlowProbeError`/`RagflowProvisionError`/`OSError` 从同一 `MATERIAL_RAG_UNAVAILABLE` 拆成 `MATERIAL_RAG_PROBE_FAILED` / `MATERIAL_RAG_PROVISION_FAILED` / `MATERIAL_RAG_NETWORK_FAILED`。不扩 GRANT，不加 retry/timeout。
- 目标测试 `test_material_rag_finish_policy_allows_terminal_without_broadening` 再跑 `Ran 1 / OK`（累计 5 次：任务1 红/绿、围栏 红/绿、本批绿）。为改 allowlist 另跑 `test_scanner_os_errors_are_split_by_errno` `Ran 1 / OK`。`git diff --check=0`。不是 smoke。开始 verify 3/6。

## 2026-08-17 02:18｜finish RLS｜v1 后唯一新因果 + 围栏 UPDATE policy

- 本窗口 verify 1/6：`./scripts/localctl material-rag-verify` 墙钟 `197658.251ms` exit 2，stdout 空。保全 `/private/tmp/anhuan-material-rag-finish-rls-20260817/v1`（0700/0600）。根锚 `6178634876075009d613ab62e742ae3d4cab2df828f66109437be0ee547611a5`。
- 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`。INDEX `PRIMARY_PROCESS`/`failed`/`MUTATION_FENCE`/`FINISH_TRUE`/`finish_sqlstate=NONE`/`lease_present=false`/`lease_live=false`/`reason_token=MATERIAL_VERSION_NOT_INDEXABLE`。verifier `exited/1`，`oom_killed=false`；其余 11 服务 healthy。专属 C/V/N=0；共享 15 exited 未变。
- 42501 已证伪为当前首因：terminal finish 已写入。未设 `f1.enterprise_id`（会经 tenant_boundary 打开整租户 upload_task 的 UPDATE/FOR SHARE）。未扩 GRANT。未弱化 `FOR SHARE OF active_job, task`。
- 本批因果：`SELECT FOR SHARE` 走 UPDATE policy；`upload_task` 仅有 SELECT worker policy + 需 `enterprise_id` 的 tenant_boundary，围栏 JOIN 空行 → `MATERIAL_VERSION_NOT_INDEXABLE`。候选：`material_rag_source_upload_worker_update`，USING/WITH CHECK 复用 `source_upload_worker`。
- 同一目标测试本批红→绿：先 `FAILED (failures=1)`（缺 `material_rag_source_upload_worker_update`，`0.001s`），再 `Ran 1 test in 0.000s` / `OK`。`git diff --check=0`。该方法累计调用 4 次（任务1 红/绿 + 本批 红/绿）。不是 smoke。
- 理由（相对建议的 session `enterprise_id` GUC）：租户安全优先；lease-scoped UPDATE policy 不打开整租户。开始 verify 2/6。

## 2026-08-17 02:06｜finish RLS｜开工回执

- 目标：证实 worker SELECT `job_target` 不接受 terminal 新行；最小改 SELECT policy 为 `job_target OR job_after_update`；删 v2 已证伪的 repository GUC 预置；最多 6 次 verify，得到 VERIFY_OK 或唯一可修 blocker。
- 顺序：任务0核对照 → 任务1红灯测试 → SELECT policy 绿灯 → 任务2 自治 verify → 任务3 收口。
- 任务0：cwd/branch/HEAD 合同；dirty=14；`git diff --check=0`；无并发 verify；专属 C/V/N=0；共享 15 exited；Ark lstat 合同；v1/v2 根锚与 INDEX `FINISH_EXCEPTION/42501` 吻合。
- 最大风险：把 UPDATE 改成 `USING(true)` 或扩大 GRANT；把 42501 误判成缺 EXECUTE。
- 额度：120 分钟、最多 6 次 `./scripts/localctl material-rag-verify`。不 commit/push。成功一次即停。
- 当前：任务0通过。任务1红→绿：`test_material_rag_finish_policy_allows_terminal_without_broadening` 先 FAILED（缺 `job_select_target`），再 `Ran 1 / OK`；`git diff --check=0`。SELECT policy 改为 `job_target OR job_after_update`；UPDATE 仍 `USING(job_target) WITH CHECK(job_after_update)`；已删 repository 重复 GUC 预置。开始任务2 第 1/6 次 verify。


## 2026-08-17 00:32｜一次性打通｜任务4 止损

- 任务3：`/Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest tests.test_material_rag.MaterialRagStaticBoundaryTests.test_scanner_os_errors_are_split_by_errno` → `Ran 1 test in 0.147s` / `OK`（复跑 `0.144s` / `OK`）。failures/errors/skipped=0。`git diff --check=0`。未写成 `SMOKE_PASSED`。
- 任务1/2 已落地：worker `ProcessOutcome` 固定枚举；内层不再把 `LeaseLost` 压成 `False`；失租不强写 failed；INDEX 11 键；scope-change 先 `ORDER BY task.id FOR UPDATE OF task` 再复查未 released；f1_0015 `guard_document_record_scope`。
- 任务4：授权 4 次，已用 2 次后停止。两次均为 exit 2、stdout 空、verifier `exited/1/oom_killed=false`、其余 11 服务 `running/healthy/exit 0`、专属 C/V/N=0、共享 15 exited 未变。无 preview/scanner/provider/BUILD。无 OOM/137。
- v1 `201824.273ms` INDEX `PRIMARY_PROCESS`/`running`/`MUTATION_FENCE`/`outcome=FINISH_EXCEPTION`/`finish_sqlstate=42501`/`lease_present=true`/`lease_live=true`/`lease_source=NONE`/`reason_token=NONE`。根锚 `63b4203c5d16b766d364e2adf2e150e9fe67dac6216a706dff5e6c69c9967832`。
- 对 `finish_job` 会话预置 `f1.material_rag_job_id`/`f1.material_rag_lease_token`（与 `claimed_session` 同构）后跑 v2 `196243.145ms`，INDEX 证据 SHA256 与 v1 相同 `eee539814087ec1a526e3a39b09b7874a19bffe87a5b303efd3c57e5b0d7a718`。按「同一完整因果签名修后再现」停止，未跑 v3/v4。
- 建议保留该 GUC 预置：未扩大权限，也未改变本因；下一动作不要再盲加 session GUC。
- 保全：`/private/tmp/anhuan-material-rag-closeout-20260817`（0700）。`ARK_KEY_ROTATION_REQUIRED`。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 23:02｜一次性打通｜开工回执

- 目标：消除 worker False 黑洞并补 INDEX 租约三布尔；关闭 scope/release 竞态；目标检查绿后最多 4 次完整 verify，得到 VERIFY_OK 或唯一可修因果 blocker。
- 顺序：任务0核对照 → 任务1红灯再实现 → 任务2 scope/release 锁序 → 任务3 目标检查 → 任务4 自治 verify。
- 任务0：cwd 合同；branch `codex/material-rag-scanner-protocol` HEAD `272a987`；dirty=10；`git diff --check=0`；无并发 verify；专属 C/V/N=0；共享 15 exited；Ark key lstat 合同成立。
- 最大风险：把 token/ID/SQL/异常正文打进 INDEX；把失租强写成 failed；给 worker 增加 record UPDATE 或拆掉 `active_job,task` 围栏。
- 额度：120 分钟、最多 4 次 `./scripts/localctl material-rag-verify`。不 checkout/stash/reset/commit/push。不扩 grant/BYPASSRLS。
- 当前：任务0通过。任务1/2/3 见 2026-08-17 00:32 节。任务4 已用 2/4 次后止损。

## 2026-08-16 20:40｜自治收口窗口

- 目标：在 `codex/material-rag-scanner-protocol` 保留全部 dirty；阶段 A 恢复 `FOR SHARE OF active_job, task`、补全 INDEX buffer、扩展并跑绿同一目标 unittest；阶段 B 最多 4 次 `./scripts/localctl material-rag-verify`。
- 阶段 A：目标检查 `/Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest tests.test_material_rag.MaterialRagStaticBoundaryTests.test_scanner_os_errors_are_split_by_errno` → `Ran 1 test in 0.118s` / `OK`（收口后复跑 `0.075s` / `OK`）。INDEX helper 只 record，`main()` 开始 clear，stderr redirect 结束后 emit；无 job 证据时 NONE fallback。
- 阶段 B 额度：4 次，已用 2 次后停止。两次均为 exit 2、stdout 空、verifier `exited/1/oom_killed=false`、其余 11 服务 `running/healthy/exit 0`、专属 C/V/N=0、共享 15 exited 未变。无 preview/scanner/provider/BUILD。
- v1 `196.749s` INDEX `PRIMARY_PROCESS`/`running`/`MUTATION_FENCE`/`reason_token=NONE`。v2 `203.342s` 同一行（证据文件 SHA256 与 v1 相同）。围栏补 `f1.enterprise_id` 未改变该故障，已撤回。
- 止损：同一固定原因修复后复现。未跑 v3/v4。未弱化时间围栏。`ARK_KEY_ROTATION_REQUIRED`。
- 保全根目录：`/private/tmp/anhuan-material-rag-closeout-20260816`（0700）。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 17:49｜120分钟自治收口

- 目标：在 `codex/material-rag-scanner-protocol` 保留全部 dirty，对 `PJ_PRIMARY_INDEX`/`PROCESS_DEMO_JOB`/`DB_OTHER` 做 DB 精确取证、红绿目标测试、证据成立后最小修复，并推进完整 material-rag-verify。
- 额度：6 批修改、6 次 verify，全部用尽。目标检查末次 `Ran 1 test in 0.070s` / `OK`。
- 本窗口 6 次 `./scripts/localctl material-rag-verify` 均为 exit 2、stdout 空、verifier `exited/1/oom_killed=false`、其余 11 服务 `running/healthy/exit 0`、专属 C/V/N=0、共享 15 exited。无 preview/scanner/provider/BUILD。
- v1 `207s` INTERNAL `DB_DATA`/`PROCESS_DEMO_JOB`/`sqlstate=NONE`。v2 `198s` 同三元组（LargeBinary 未改变该故障）。v3 `196s` `TEXT_NUL`/`SCOPE_LOCK`。v4 `198s` `DB_PROGRAMMING`/`42501`/`MUTATION_FENCE`。v5 `214s` 与 v6 `198s` 当轮首码 `LOCAL_MATERIAL_RAG_INDEX_FAILED`；v6 INDEX 证据 `DEGRADED MISSING`（写入被 verifier stderr 重定向丢弃）。
- 已落地：scope lock 改为 `hashbyteaextended`+bytes；fence 改为 `FOR SHARE OF active_job`。止损：6 次 verify 用尽。`ARK_KEY_ROTATION_REQUIRED`。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 16:05｜asyncio task 内记录 + operation

- 目标：在 `codex/material-rag-scanner-protocol` 保留全部 dirty，对 `PJ_PRIMARY_INDEX`/`PROCESS_DEMO_JOB`/`DB_OTHER` 做 DB 精确取证、红绿目标测试、证据成立后最小修复，并推进完整 material-rag-verify。
- 顺序：先扩展 INTERNAL 的 sqlstate/db_token 与更细 DB 类 → 目标检查红绿 → verify；仅当证据唯一指向白名单缺陷时再最小修并再 verify。
- 额度：120 分钟、最多 6 批可证伪修改、最多 6 次完整 `./scripts/localctl material-rag-verify`。本消息已字面授权 verify 与专属栈启动/cleanup。中途不向用户汇报。
- 只改白名单路径。保留 Dockerfile `chmod -R a+rX`。不 checkout `codex/material-rag`。不读 key。
- 最大风险：把 RAISE 正文/SQL/类名打进证据；把未捕获 DB 异常改成 INDEX_FAILED 假绿；盲加 retry/timeout/内存。
- 止损：成功一次；红线；同一精确故障修复后重复 2 次；6 次 verify 或 120 分钟用尽。
- 当前：窗口进行中。`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 16:05｜asyncio task 内记录 + operation

- 目标：在 `_run_async` except 内记录 phase/operation；INTERNAL 四字段；假绿改为真实 asyncio.run。授权 1 次 verify。
- 顺序：先红后绿目标检查 → 1 次 verify。UNKNOWN / operation=UNKNOWN / 畸形 / 重复即停；得到具体三元组也先汇报不猜修。
- 只改 verifier / localctl / 本测试。保留全部 dirty。
- 目标检查：`Ran 1 test in 0.070s` / `OK`。唯一 1 次 verify：墙钟 `204.887s` exit `2`。当轮首码 `LOCAL_MATERIAL_RAG_INTERNAL_ERROR`。INTERNAL `{"error_class":"DB_OTHER","operation":"PROCESS_DEMO_JOB","phase":"PJ_PRIMARY_INDEX","primary_preserved":true}`。verifier `exited/1/oom_killed=false`；其余 healthy。专属 C/V/N=0；共享 15 exited。按「得到具体三元组先汇报不猜修」停止。`ARK_KEY_ROTATION_REQUIRED`。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 15:23｜PJ 子阶段 + 安全 error_class

- 目标：PROCESS_JOBS 拆成 11 个 PJ 子阶段；扩展 error_class；修超长行 MALFORMED 与 dispose `primary_preserved`；最多再 2 次 verify。
- 顺序：先红后绿目标检查 → 第1次 verify；仅当 PJ 子阶段+类别可证伪时最小修 → 目标测试 → 第2次 verify。UNKNOWN/OTHER/重复/OOM/Ark/隔离/共享漂移/cleanup 非零即停。
- 本批只改 verifier / localctl / 本测试。不 checkout、不 stash、不改 Dockerfile。
- 最大风险：把 PROCESS_JOBS 业务重排；输出异常类名/SQL；用下一轮掩盖 OTHER。
- 目标检查：`Ran 1 test in 0.075s` / `OK`。第1次 verify：`./scripts/localctl material-rag-verify` 墙钟 `195.718s` exit `2`。stdout 空。当轮首码 `LOCAL_MATERIAL_RAG_INTERNAL_ERROR`。INTERNAL `{"error_class":"DB_OTHER","phase":"UNKNOWN","primary_preserved":true}`。FAILURE_EVIDENCE：verifier `exited/1/oom_killed=false`；其余 11 个服务 `running/healthy/exit 0`。无 preview/scanner/provider/BUILD。cleanup 后专属 C/V/N=0；共享 15 exited 未变。
- 止损：阶段为 `UNKNOWN`，未跑第 2 次 verify。`ARK_KEY_ROTATION_REQUIRED`。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16 14:24｜INTERNAL 诊断自治窗口

- 目标：先让 `INTERNAL_ERROR` 留下安全阶段证据，再在白名单内有限重放；成功一次即停。
- 顺序：任务0核对 → 任务1 INTERNAL evidence 先红后绿 → 最多 3 次 verify；同一固定码+阶段+错误类别两次即停。
- 任务0：分支 `codex/material-rag-scanner-protocol` HEAD `272a987`；9 dirty；`git diff --check` 0；无并发；专属 C/V/N=0；共享 15 exited；key lstat 合同成立。现役码仍是 `INTERNAL_ERROR`，不得归因 STACK_START_FAILED。
- 最大风险：dispose 覆盖主错误导致假 INTERNAL；证据泄漏 traceback/路径；把未观测写成 NOT_REACHED。
- 额度：60 分钟、最多 3 批代码、3 次完整 verify。不改 Dockerfile 权限、不读 key。结束后只记 `ARK_KEY_ROTATION_REQUIRED`。
- 任务1：目标检查先红（缺 `_INTERNAL_EVIDENCE_PHASES`）后绿。命令：`/Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest tests.test_material_rag.MaterialRagStaticBoundaryTests.test_scanner_os_errors_are_split_by_errno` → `Ran 1 test in 0.068s` / `OK`。代码批 1/3：verifier 阶段枚举 + localctl 提取/canonicalize + dispose 主错误保留。未改 Dockerfile。
- 任务2 第1轮：`./scripts/localctl material-rag-verify` 墙钟 `196.451s` exit `2`。stdout 空。stderr 首个固定码 `LOCAL_MATERIAL_RAG_INTERNAL_ERROR`。INTERNAL `{"error_class":"OTHER","phase":"PROCESS_JOBS","primary_preserved":true}`。FAILURE_EVIDENCE：verifier `exited/1/oom_killed=false`；其余 11 个服务 `running/healthy/exit 0`（含 egress-proxy）。无 preview/scanner/provider/BUILD 行。cleanup 后专属 C/V/N=0；共享 15 exited 未变。
- 止损：`OTHER` 不能唯一指向白名单内可证伪缺陷；未跑第 2/3 次 verify；未盲加 retry/timeout/内存。`ARK_KEY_ROTATION_REQUIRED`。
- 当前：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 2026-08-16｜egress 权限批次

- 目标检查红→绿 `test_compose_has_no_host_ports_and_only_proxy_has_egress` `Ran 1 / OK`。Dockerfile COPY 后 `chmod -R a+rX`。
- 1 次 verify 约 200s exit 2，首个码 `LOCAL_MATERIAL_RAG_INTERNAL_ERROR`。verifier `exited/1`。egress-proxy `running/healthy/exit 0`。无 preview/scanner/provider/BUILD。专属残留 0。不得用 STACK_START_FAILED 覆盖。

## 2026-08-15 23:03｜pip 韧性 + STACK_START

- 任务1 红→绿 `test_scanner_os_errors_are_split_by_errno` `Ran 1 / OK`。
- 任务2 第1轮约 621s exit 2，`LOCAL_MATERIAL_RAG_STACK_START_FAILED`；egress-proxy 当时 `restarting/unhealthy/exit 2`。
