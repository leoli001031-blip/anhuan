# MATERIAL RAG Blocked

## 2026-08-21｜multi-stage SIGKILL matrix checkpoint｜开工

- 无。
- 任务0 相符。本轮只封已验收矩阵 checkpoint；不重跑 Docker。提交/远端身份以 Git 与 PR head 为准。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜SIGKILL checkpoint + 三阶段矩阵｜收口

- 无。
- 阶段A：`6dbb326` 已普通 push，PR #2 仍 draft，base 未改。阶段B：三阶段矩阵与旧单阶段门全绿；合同 `Ran 60 / OK` skipped=0。专属 C/V/N=0。共享 fingerprint 不变。矩阵 delta 未 stage/commit/push。未改 compose / restore_recovery / f1_0015 / worker / repository / ragflow_adapter / 默认 backup / UAT / 依赖 / workflow。未读 Ark。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。断电未测：`CRASH_RECOVERY_POWER_LOSS_NOT_TESTED`。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜SIGKILL checkpoint + 三阶段矩阵｜开工

- 无。
- 任务0 相符：HEAD=`21e7eea81107eaf73dcc5ca125c754b67e2c7224` dirty=9 均在白名单。origin/PR #2 head 均为 21e7eea。阶段A 授权精确 commit+普通 push；阶段B 本地不提交。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜crash 假绿封口｜收口

- 无。
- 合同先红后绿 `Ran 56 / OK` skipped=0；crash Docker 一门终绿。专属 C/V/N=0。共享 fingerprint 不变。未改 compose / restore_recovery / f1_0015 / worker / repository / ragflow_adapter / 默认 backup / UAT / 依赖 / workflow。未读 Ark。未 commit/push，未改 PR #2（head 仍为已 push 的 21e7eea）。实际闭集 9 文件，不是 7。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。断电/全阶段 crash 未测。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜crash 假绿封口｜开工

- 无。
- 任务0 相符：HEAD=`21e7eea81107eaf73dcc5ca125c754b67e2c7224` staged=0 dirty=9 均在白名单。origin 与 PR #2 head 均为 21e7eea。不 fetch/rebase。不 commit/push。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜DB_RESTORED SIGKILL crash recovery｜收口

- 无。
- 合同先红后绿 `Ran 54 / OK`；crash Docker 一门终绿；v3 非回归 attempt2 终绿。专属 C/V/N=0。共享 fingerprint 不变。未改 compose / f1_0015 / worker / repository / ragflow_adapter / 默认 backup / UAT / 依赖 / workflow。未读 Ark。未 commit/push，未改 PR #2（head 仍为已 push 的 21e7eea）。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。断电/全阶段 crash 未测。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜DB_RESTORED SIGKILL crash recovery｜开工

- 无。
- 任务0 相符：HEAD=`21e7eea81107eaf73dcc5ca125c754b67e2c7224`，worktree clean，staged=0，`git diff --check=0`。origin 同分支与 PR #2 head 均为 21e7eea。不 fetch/rebase。不 commit/push。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_RUNTIME_NOT_TESTED / BACKEND_CHECKPOINT_READY / NOT_PUSHED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 自主修通｜收口

- 无。
- 合同 2/8 绿；Docker 2/4 终绿。专属 C/V/N=0。共享 fingerprint 不变。未改生产三文件 / `local_backup.py` / RLS / f1_0015 / 共享 `anhuan-f1`。未读 Ark。未 commit/push，未更新 PR #2。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。crash journal runtime 未测。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_RESTORE_ABORT_CLEANUP_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / CRASH_RECOVERY_RUNTIME_NOT_TESTED / BACKEND_CHECKPOINT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 自主修通｜开工

- 无。
- 续跑相符：HEAD=`f0e60a41` dirty=12 staged=0；旧包根 `bf29558e…525f`。合同断言定位错误待修，未进 Docker。

当前状态：`MATERIAL_RAG_RESTORE_ABORT_CLEANUP_IMPLEMENTED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / TARGETED_TEST_BLOCKED / RUNTIME_REVALIDATION_PENDING / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 正常验证｜收口

- 唯一合同检查失败（一次，未重跑）：`Ran 46 tests in 0.225s / FAILED (failures=1)`，exit=1，wall=0.322s，skipped=0。
- 固定签名：`FAIL: test_restore_injects_after_volume_or_raw_db_restore` → `AssertionError: 'com.docker.compose.project=' not found`。`_list_project_handles` 只调用 `_compose_project_filter()`，合同把过滤串断言打在了错误的 getsource 范围。失败后未改代码、未重跑。
- 无 Docker / Ark / 共享栈写 / Git 外部写入。无白名单外漂移。冻结 compose/probe/restore_maintenance SHA 未变。
- 下一跳（须另开窗口）：修正该合同断言后再跑唯一合同一次；绿后才允许 `./scripts/localctl material-rag-backup-restore-check` 一次。本轮不得恢复 runtime PASSED。

当前状态：`MATERIAL_RAG_RESTORE_ABORT_CLEANUP_IMPLEMENTED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / TARGETED_TEST_BLOCKED / RUNTIME_REVALIDATION_PENDING / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 正常验证｜开工

- 无。
- 任务0 相符：HEAD=`f0e60a41c503c49504fdb208f06b5aad40d3e0c9`，dirty=12（6M+6??），staged=0，`git diff --check=0`。offline-v1 根 `503c1544…0db72` 与 12-manifest 一致。旧唯一检查 Ran45/errors=1 不当绿。
- 不 commit/push，不读 Ark，不开放用户 restore，不手工 Docker cleanup。不是 production。

当前状态：`MATERIAL_RAG_RESTORE_ABORT_CLEANUP_IMPLEMENTED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / TARGETED_TEST_BLOCKED / RUNTIME_REVALIDATION_PENDING / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 身份清理 + journal｜收口

- 唯一合同检查失败（一次，未重跑）：`Ran 45 tests in 0.196s / FAILED (errors=1)`，exit=1，wall=0.293s，skipped=0。
- 固定签名：`ERROR: test_restore_injects_after_volume_or_raw_db_restore` → `ValueError: substring not found`（查找 `abort_new_restore_resources(`）。当时实现走 `_recovery(abort_new_restore_resources,`，getsource 无 `(`。失败后已直连调用并改合同；不得把本失败当绿。
- 无 Docker / Ark / 共享栈写 / Git 外部写入。无白名单外漂移。冻结 compose/probe/localctl/restore_maintenance SHA 未变。
- 下一跳（正常验证，须另开窗口）：① 唯一合同检查再跑一次，要求 Ran≥38、exit=0、skipped=0；② 通过后再跑专属 Docker 门 `./scripts/localctl material-rag-backup-restore-check`（身份级 abort，禁止再比 C/V/N 数量）。本轮不得恢复 runtime PASSED。

当前状态：`MATERIAL_RAG_RESTORE_ABORT_CLEANUP_IMPLEMENTED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / TARGETED_TEST_BLOCKED / RUNTIME_REVALIDATION_PENDING / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-20｜restore abort 身份清理 + journal｜开工

- 无。
- 任务0 相符：HEAD=`f0e60a41c503c49504fdb208f06b5aad40d3e0c9`，dirty=11，staged=0。partial-failure 包根 `32c65439…8c0f` 与 11 文件工作树一致。
- 现役降级：成功路径仍作历史观测；`MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_BLOCKED`（计数假绿未封）。不 commit/push，不读 Ark，不开放用户 restore，不跑 Docker。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_SUCCESS_PATH_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_BLOCKED / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-19｜partial-failure cleanup｜收口

- 无。
- 合同 32 红→绿；live 1/2 唯一尾码 `LOCAL_MATERIAL_RAG_BACKUP_RESTORE_OK`；clone 合同 32。四阶段 mutation 后失败清理已证明。正式用户 restore 仍未开放，不构成现役工程 blocker。
- 未改生产三文件 / `local_backup.py` / RLS / f1_0015 / 共享 `anhuan-f1`。未读 Ark。未 commit/push，未更新 PR #2。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / MATERIAL_RAG_POST_RESTORE_REBUILD_PASSED / MATERIAL_RAG_RESTART_RECOVERY_PASSED / MATERIAL_RAG_RESTORE_READ_ISOLATION_PASSED / BACKEND_CHECKPOINT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-19｜partial-failure cleanup｜开工

- 无。
- 任务0 相符：HEAD=`f0e60a41c503c49504fdb208f06b5aad40d3e0c9`，dirty=11，staged=0，`git diff --check=0`。evidence-v2 根 `08df6321…eb985` 自洽但不是当前 dirty 根。
- 现役降级：成功路径四项仍成立；`MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_NOT_PROVEN`；`BACKEND_CHECKPOINT_NOT_READY`。不 commit/push，不读 Ark，不开放用户 restore。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_SUCCESS_PATH_PASSED / MATERIAL_RAG_POST_RESTORE_REBUILD_PASSED / MATERIAL_RAG_RESTART_RECOVERY_PASSED / MATERIAL_RAG_RESTORE_READ_ISOLATION_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_NOT_PROVEN / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-19｜backup/restore 假绿封口 + 证据包｜收口

- 无。
- 机器门已绿：合同 27、合并 155、localctl 唯一尾码、clean-clone 155。正式用户 restore 仍未开放，不构成现役工程 blocker。
- 未改生产三文件 / `local_backup.py` / RLS / f1_0015 / 共享 `anhuan-f1`。未读 Ark。未 commit/push，未更新 PR #2。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。不是 production。

当前状态：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_POST_RESTORE_REBUILD_PASSED / MATERIAL_RAG_RESTART_RECOVERY_PASSED / MATERIAL_RAG_RESTORE_READ_ISOLATION_PASSED / BACKEND_CHECKPOINT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-19｜backup/restore 假绿封口 + 证据包｜开工

- 无。
- 任务0：HEAD=`f0e60a41c503c49504fdb208f06b5aad40d3e0c9`，10 dirty（6M+4??），staged=0，`git diff --check=0`。旧 ≥12/6 不追认合规。旧 b430 根锚不可从 v1 独立复算。
- 现役降级：`TARGETED_TEST_PASSED / BACKUP_RESTORE_RUNTIME_BLOCKED / EVIDENCE_PACKAGE_INCOMPLETE / BACKEND_CHECKPOINT_REVIEW_REQUIRED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`。
- 旧三项 RUNTIME/REBUILD/RESTART PASSED 仅历史观测。不 commit/push，不更新 PR #2，不读 Ark。不是 production。

当前状态：`TARGETED_TEST_PASSED / BACKUP_RESTORE_RUNTIME_BLOCKED / EVIDENCE_PACKAGE_INCOMPLETE / BACKEND_CHECKPOINT_REVIEW_REQUIRED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`


## 2026-08-19｜backup/restore 专属机器门｜收口

- 无。
- 专属机器门已绿：live v8、合并 145、localctl `LOCAL_MATERIAL_RAG_BACKUP_RESTORE_OK`、clean-clone 145。正式面向用户的 restore 命令仍未开放，不构成现役工程 blocker。
- 未改生产三文件 / `local_backup.py` / RLS / f1_0015 / 共享 `anhuan-f1`。未读 Ark。未 commit/push，未更新 PR #2。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。真实 live retrieval：`LIVE_RETRIEVAL_UAT_DEFERRED`。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / MATERIAL_RAG_BACKUP_RESTORE_DESIGN_READY / MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_POST_RESTORE_REBUILD_PASSED / MATERIAL_RAG_RESTART_RECOVERY_PASSED / BACKEND_CHECKPOINT_READY / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`

## 2026-08-19｜backup/restore 专属机器门｜开工

- 无。
- 任务0 相符：HEAD=`f0e60a41c503c49504fdb208f06b5aad40d3e0c9`，clean，PR #2 draft，专属 C/V/N=0，共享 fingerprint 与前序一致。
- 不 commit/push，不更新 PR #2，不读 Ark。领导签字未做。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED / NOT_PRODUCTION`

## 2026-08-19｜stale 本地零写 + restore 维护原语｜收口

- 无。
- stale 本地+远端零写、六类 job 维护、非法 UPDATE 红灯与 rollback 均有 raw 红→绿。正式 restore 命令仍未实现：`BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED`。
- 未改生产三文件/RLS/DDL/Ark/共享栈。未更新 PR #2。未 commit。领导签字未做。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / MATERIAL_RAG_BACKUP_RESTORE_DESIGN_READY / BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED / BACKEND_CHECKPOINT_READY / NOT_PRODUCTION`

## 2026-08-19｜stale 本地零写 + restore 维护原语｜开工

- 无。
- 任务0 与仓/PR/hardening MANIFEST 相符。生产三文件零 diff。
- 现役 backup 降为 `BACKUP_RESTORE_DESIGN_NEEDS_REVISION`。历史 `RAW_RED_GREEN_OUTPUT_NOT_CAPTURED` / `HARDENING_CYCLE_EVIDENCE_OVERWRITTEN` 保留，不伪造补齐。
- 不更新 PR #2，不 commit/push，不破坏性 restore，不改生产。领导签字未做。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_NEEDS_REVISION / NOT_PRODUCTION`

## 2026-08-19｜生命周期证据门加固｜收口

- 无。
- 远端生命周期、stale claim 零写、SQL residual 五门均有 raw 红→绿。restore 只到可执行设计。`BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED`。
- 未改生产三文件/RLS/DDL/Ark/共享栈。未更新 PR #2。未 commit。领导签字未做。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_READY / NOT_PRODUCTION`

## 2026-08-19｜生命周期证据门加固｜开工

- 无实施不确定项。任务0 与仓/PR 相符，生产三文件零 diff。
- 现役降级：`TARGETED_TEST_PASSED / BACKEND_JOB_LIFECYCLE_EVIDENCE_INCOMPLETE / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_NEEDS_REVISION / NOT_PRODUCTION`。
- 旧生命周期 raw 红绿日志不可恢复：`RAW_RED_GREEN_OUTPUT_NOT_CAPTURED`。禁止从文档摘要伪造。
- 不更新 PR #2，不 commit/push，不 restore，不改生产。领导签字未做。不是 production。

当前状态：`TARGETED_TEST_PASSED / BACKEND_JOB_LIFECYCLE_EVIDENCE_INCOMPLETE / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_NEEDS_REVISION / NOT_PRODUCTION`

## 2026-08-19｜生命周期 / 已知ID重投 / backup 设计｜收口

- 无。
- 默认 `localctl backup/restore` 仍只证明 `f1_0014/35`；material-RAG 为 `f1_0015/38` 且含多类持久数据。本轮只落设计，不构成现役工程 blocker。源码支持把 RAGFlow/MySQL/ES/cache 当派生物、PostgreSQL+源 MinIO 当权威；禁止备份 secret/Ark/RAGFlow 原始卷。Runtime restore 明确 `BACKUP_RESTORE_RUNTIME_NOT_IMPLEMENTED`。
- 未改 RLS/GRANT/f1_0015/默认 backup/`local_backup.py`/共享 `anhuan-f1`。未读 Ark。领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。不是 production。

当前状态：`BACKEND_JOB_LIFECYCLE_INTEGRATION_PASSED / BACKEND_LEASE_FENCE_PASSED / BACKEND_REBUILD_DELETE_RESIDUAL_PASSED / KNOWN_ID_JOB_REDELIVERY_RECOVERY_PASSED / BACKUP_RESTORE_DESIGN_READY / NOT_PRODUCTION`

## 2026-08-19｜PostgreSQL checkpoint 发布 + 生命周期门｜开工

- 无。
- 本会话初工作树被还原到 HEAD，3 个新增文件缺失；已从冻结 patch 恢复。未重跑已过 97 门。不构成发布 blocker。

## 2026-08-19｜真实 PostgreSQL 后端集成门｜收口

- 无实施不确定项。真实 PostgreSQL + f1_0015 + 生产 Repository/Service 集成门、合并门、clean-clone 复现均 exit 0，skipped=0。
- Ark key 轮换不是 blocker。未读、未打印、未改、未用旧 key。现役只记 `LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED`。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。未跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。本检查点已获 commit/push/draft PR 授权，OID/PR 以 GitHub 为准。

当前状态：`BACKEND_POSTGRES_REPOSITORY_INTEGRATION_PASSED / BACKEND_RLS_SCOPE_ISOLATION_PASSED / BACKEND_TRANSACTION_RECOVERY_PASSED / BACKEND_RUNTIME_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED / NOT_PRODUCTION`

## 2026-08-19｜真实 PostgreSQL 后端集成门｜开工

- 无实施不确定项。任务0与远端基线相符：HEAD=`c58ef92bde3086e26cbd119bbbb4debe6f7eb905`，branch=`codex/material-rag-postgres-integration`，dirty=0。未写 `BACKEND_BASELINE_DRIFT`。
- Ark key 轮换不是本轮 blocker。未读、未打印、未改、未用旧 key。现役只记 `LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED`。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。不 commit/push。

当时状态：`BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`

## 2026-08-19｜现役阻塞口径

- 当前无 material-RAG 后端工程 blocker。
- Ark key 轮换已从现役 blocker 移除；真实 live retrieval 延后且未测试，但不阻塞离线、本地后端、数据库合同及其他不依赖 Ark 的开发。
- 仍待人工处理：`HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING`。仍为 `NOT_PRODUCTION`，未部署。

当前状态：`BACKEND_POSTGRES_REPOSITORY_INTEGRATION_PASSED / BACKEND_RLS_SCOPE_ISOLATION_PASSED / BACKEND_TRANSACTION_RECOVERY_PASSED / BACKEND_RUNTIME_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED / NOT_PRODUCTION`

## 2026-08-19｜生产检索信任边界｜收口

- 无新的实施不确定项。离线后端合同门与 clean-clone 合并门均 exit 0。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。未跑 headed `open`，不得写 `HUMAN_UAT_READY`。checkpoint UAT 未重跑。
- 不是 production，未部署。未 commit/push。

当前状态：`BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-19｜生产检索信任边界｜开工

- 无。任务0与 checkpoint 基线相符：HEAD=`6dd4b9158af3f8eb15922fff5bc715c9a3848f68`，branch=`codex/material-rag-scanner-protocol`，dirty=0，staged=0，untracked=0。未写 `BACKEND_BASELINE_DRIFT`。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，不得写 `HUMAN_UAT_READY`。checkpoint UAT 不重跑。
- 不是 production，未部署。不 commit/push。

当时状态：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 23:22｜租户 Select 时序/定位｜收口

- 无新的实施不确定项。本窗口机器门已过：`LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。周期1 固定码 `UAT_TENANT_SWITCH_FAILED`；一跳后周期2 通过。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。未跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。未 commit。

当前状态：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 22:57｜租户 Select 时序/定位｜开工（已被 23:22 收口）

- 无。任务0与上窗收口基线相符：HEAD=`a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，dirty=24，staged=0，专属 C/V/N=0，无控制目录，无并发 UAT，共享 15/9/1。
- 根因尚未唯一证实；本窗口按 header Select popup 时序/定位竞态验证，不得写成已确认产品 bug。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，不得写 `HUMAN_UAT_READY`。

当时状态（已被 23:22 覆盖）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 22:45｜J6 同页清空｜live 2/2 停止

- 无新的实施不确定项。本窗口末次固定码：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_OPTION_MISSING`
- 完整因果：周期2 start exit 0 → check exit 2；反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`；check `finally` 已 `down --volumes`；专属 C/V/N=0；控制目录已删除。
- 周期1 码不同：`UAT_TENANT_SWITCH_FAILED`（61s）。一跳把租户选项从 `clickElementWithText` 改为唯一可见 dropdown 的 DOM `click()` 后，周期2 变为 `UAT_TENANT_OPTION_MISSING`（41s）。J6 五字段与 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK` 未出现。
- 含义：同页清空离线已红→绿，live 在租户切换提交上停下，清空门未获 live 证明。禁止本窗口再 start。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。未 commit。

当前状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 22:07｜J6 同页清空｜开工（已被 22:45 收口）

- 无。假绿已定位：J6 fresh `/qa` 初态已空，只查 Empty，`cleared_on_failure` 字面 true。本轮用同页 prior ready + 同 requestId 503 实测清空。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 20:24 机器门两标签暂撤销为历史，因清空门未测。

当时状态（已被 22:45 覆盖）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 20:24｜J6 AntD6 选择提交｜收口（历史；清空假绿，已被 22:07 覆盖）

- 无。本窗口无新的实施不确定项；唯一 live 已通过，未继续猜修。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮未跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。未 commit。

当时状态（历史，清空假绿）：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 2026-08-18 20:07｜J6 AntD6 选择提交｜开工（已被 20:24 收口）

- 当时无新的实施不确定项。`.ant-select-item-option` 提交失败仍是高置信假说，本轮先拆 select/ask/observe_request 再唯一 live 裁决。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，成功后仍不得写 `HUMAN_UAT_READY`。

当时状态（已被 20:24 覆盖）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`

## 2026-08-18 19:43｜旅程终态证据链｜历史（旧窗口 live 2/2 已封存；已被 20:24 覆盖为文档现役）


- 本窗口末次 live 固定码：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED REQUEST_NOT_SENT`
- 有限证据：`{"actual_phase":null,"expected_phase":"unavailable","http_status":null,"journey":"J6_FAIL_CLEAR","request_seen":0}`
- 完整因果：周期2 start exit 0 → check exit 2；反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`；check `finally` 已 `down --volumes`；专属 C/V/N=0；控制目录已删除。
- 含义：J1–J4 已过（URL `?query=` 预选了目标 query）；J6 必须把默认 `provider.shared` 改成 `fail.clear`，CDP 鼠标点击 option 未让 antd 6 提交选中值，故 `request_seen=0`。不得把离线 `Ran 48 / OK` 写成 live 通过。
- 额度：本窗口 live 2/2 已用尽。禁止再跑 `material-rag-uat-start/check/stop`，直到下一授权窗口按 J6 证据做 DOM option click。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮未跑 headed `open`，不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。未 commit。

当时状态（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`

## 2026-08-18 19:07｜旅程终态证据链｜开工（已被 19:43 覆盖为文档现役）

- 无新的实施不确定项。旧窗口唯一 live 码仍为 `UAT_PHASE_MISSING`（六旅程共用），本窗口先拆证据链再 live。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。本轮不跑 headed `open`，成功后仍不得写 `HUMAN_UAT_READY`。

当时状态（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。

## 2026-08-18 18:53｜live 浏览器第一阶段门失败｜历史（旧窗口；已被 19:43 覆盖）

- 本窗口唯一 live 固定码：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_PHASE_MISSING`
- 完整因果签名（周期2/2）：start exit 0 → check exit 2；反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`；浏览器 stderr 唯一失败码 `UAT_PHASE_MISSING`；check `finally` 已 `down --volumes`；专属 C/V/N=0；控制目录已删除。
- 含义：真实浏览器链在 `waitForPhase` 上失败（J1 `ready` / J4 `empty` / J6 `unavailable` 共用此码）。61s 墙钟与登录后第一次 20s phase 等待相符。双合法租户隔离的 live 摘要键（`valid_tenant_count=2` 等）未出现。不得把 unittest 41/OK 写成 live 通过。
- 已落地、未 live 证明：A/B catalog 原样映射、CRM 闭集名绑定、资源同名异主拒绝、失败清理、空 lock 可删控制目录、`HUMAN_UAT_URL` / `material-rag-uat-open`。
- 额度：live 周期 2/2 已用尽。禁止再跑 `material-rag-uat-start/check/stop`，直到下一授权窗口先拆分 phase 失败码。
- 现役保留：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 领导签字未做：`HUMAN_UAT_SIGNOFF_PENDING`。不得写 `HUMAN_UAT_READY`。
- 不是 production，未部署。未 commit。

当时状态（历史）：`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。

## 2026-08-18 18:12｜双合法租户隔离失效｜历史（20:24 live 已证明隔离；当时三个 UAT 通过标签已撤销）

- 当时实施阻塞：`catalog_enterprise_for_tenant()` 对所有合法租户都返回合成 `ENTERPRISE_A`；跨租户 404 只测了非成员 UUID。三个 UAT 通过标签已撤销。
- 离线测试已改为按认证 tenant 原样映射；live 未证明。

## 2026-08-18 17:34｜合成浏览器 UAT 收口｜历史（已被 18:12 撤销三个 UAT 通过标签）

- 当时现役（历史，含 `HUMAN_UAT_READY`，已撤销）：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。
- 隐藏验收发现双合法租户未隔离，标签撤销，不再现役。

## 2026-08-18 14:54｜产品 UAT 机器门｜历史（已被 17:34 合成浏览器 UAT 覆盖为文档现役）

- 当时机器门已过（历史）：`UAT_MACHINE_GATE_PASSED`。不是 `UAT_PASSED`。
- 现役阻塞：`LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION`（`ARK_KEY_ROTATION_REQUIRED`）。未读、未打印、未改、未用旧 key。
- 人工产品走查未签：`HUMAN_UAT_SIGNOFF_PENDING`。
- 不是 production，未部署。无跨租户泄漏、无权限扩大。不标 `NEEDS_USER`（本轮不需要旧 key 即可完成离线机器门）。

当时状态（历史）：`UAT_MACHINE_GATE_PASSED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。

## 2026-08-18 03:22｜合同漏项检查点｜历史（已被 14:54 UAT 机器门覆盖为文档现役，检查点本身仍有效）

- 当时无实施阻塞。状态为 `TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY / NOT_PRODUCTION`。
- `ARK_KEY_ROTATION_REQUIRED`。UAT 当时未授权。旧 20260817 检查点只读。

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

当时状态（历史）：`TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION`。已被 2026-08-18 检查点窗口收口，不再现役。

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
