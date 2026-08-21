# material-RAG backup/restore 下一轮可实施设计

现役：`MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / MATERIAL_RAG_PARTIAL_FAILURE_CLEANUP_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / BACKEND_CHECKPOINT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`。
2026-08-21 operator preflight 已落地：`./scripts/localctl material-rag-restore-preflight --backup-id <闭集ID>`。只读核验 manifest/owner/mode/link、project/parent/scope、PostgreSQL dump、MinIO tree、38 表、两个数据卷、共享身份、package `f1_0015` → target `f1_0016`。`destructive_started=0` `ready_to_apply=0`。禁止 apply/down/删卷/pg_restore/MinIO 回放。正式面向用户的 restore 命令仍未开放。
2026-08-21 三阶段 SIGKILL 矩阵已验收并封入本 checkpoint：内部命令 `material-rag-backup-restore-crash-matrix-check` 覆盖 `VOLUMES_REPLACED / DB_RESTORED / MINIO_REPLAYED`；各阶段真实 SIGKILL 后 fresh process 按 receipt+journal 精确恢复；MINIO_REPLAYED 另核 live 对象树与 package 一致。PREPARED 只离线合同。合同 `Ran 60 / OK`。旧单阶段 `material-rag-backup-restore-crash-check` 非回归。提交与远端身份以 Git 与 PR head 为准。未写 `CRASH_RECOVERY_RUNTIME_PASSED`。正式面向用户的 restore 命令仍未开放。
旧证据包 `material-rag-crash-false-green-20260820-v1`、checkpoint 推送包 `material-rag-sigkill-checkpoint-push-20260820-v1`、矩阵包 `material-rag-crash-matrix-20260820-v1` 只读。
2026-08-20 SIGKILL 单阶段门与假绿封口已纳入当时 parent checkpoint（历史记录，SHA 见 Git）。`NOT_PUSHED` 仅为当时矩阵 delta 的历史标签，不再冒充现役。
2026-08-20 crash 假绿封口（历史）：`fallback_cleanup_used` 必须反映真实执行；stop 后若仍有专属资源，允许精确三标签诊断清理，但本门失败、不得输出 OK。tamper 只有 `returncode=2`、stdout 空、stderr 恰为 `RESOURCE_LABEL_MISMATCH\n` 且 5 个 abort ID 仍在，才 `tamper_rejected=1` / `tamper_reason_verified=1`。两次间隔 ≥0.5s 的 C/V/N=0 锁定 `stable_zero_observations=2`。当时合同 `Ran 56 / OK`。
2026-08-20 DB_RESTORED SIGKILL：内部命令 `material-rag-backup-restore-crash-check`；child 在 journal 原子写入 `DB_RESTORED` 后暂停，监督进程 SIGKILL（`hard_death_signal=9`），第二 fresh process 仅凭 0600 receipt+journal 精确恢复。错标签反向零删除后再删新 2 卷+3 容器。当时合同 `Ran 54 / OK`。未写 `CRASH_RECOVERY_RUNTIME_PASSED`（不冒充断电/全阶段）。

## 本轮已封的三个假绿

1. MinIO：live canonical tree 含 bucket/key/size/正文 SHA（对外字段为摘要）；同数同大小不同正文 → `MINIO_BODY_SHA_MISMATCH`。
2. restart：PG+MinIO stop/start 后，父进程卸载 fake 并 `dispose_runtime`，独立 `post_restart_probe.py` 子进程重连 MinIO/PG，走生产 repository/service。
3. 隔离：恢复重建后 A/B 正向检索；transport 注入双方及兄弟 scope 候选，最终 `cross_tenant_visible=0`、`cross_scope_visible=0`。
4. cleanup：删 fallback C/V/N 前逐项核对三标签；restore/maintenance/rebuild/restart 各注入一次失败。restore abort 现改为精确资源身份：volume id=`sha256(Name\\0CreatedAt\\0Mountpoint)`，容器/网络用 Docker hex ID；失败只删三标签闭合的新容器与新数据卷。`capture_injected` 的 restore 分支禁止用 C/V/N 数量当成功。内部 journal 阶段闭集 PREPARED→VOLUMES_REPLACED→DB_RESTORED→MINIO_REPLAYED，未完成可 recover→RECOVERED。

## 为何现役 backup 不能直接用于 material-RAG

源码证据：

- `infra/f1/local_verify.py` 默认工程 head 锁定 `f1_heads == ("f1_0014",)`，业务计数表闭集 `P2_P7_TABLES` 共 35 张。
- `scripts/localctl` `_business_snapshot` / restore 后身份核对只用 `P2_P7_TABLES[:manifest_table_count]`。
- `infra/f1/alembic/versions/f1_0015_material_rag.py` 另增 3 张 FORCE RLS 表：`material_rag_scope_binding`、`material_rag_unit`、`material_rag_job`。专属目录因此是 `f1_0015 / 38`。
- 专属运行栈 `infra/f1/docker-compose.material-rag.yml` 除 PostgreSQL 与源 MinIO 外，还有 MySQL / Elasticsearch / RAGFlow objectstore / cache / Redis 等卷。

因此默认 `localctl backup/restore` 只能证明 closeout 的 `f1_0014/35`。把它打到 material-RAG 卷上会漏 3 张表的计数身份，也会误把可重建派生物当权威数据。

## 权威 / 派生 / 禁止备份

### 权威（下一轮必须进包）

1. PostgreSQL 逻辑转储（现役 `database.dump` 形态可沿用）：含 `f1_0015` 后的 f1 业务库，不只 38 张计数表。38 只是身份摘要闭集。
2. 源 MinIO 对象树（现役 `minio-data`）：P3 原件是重建 unit 正文的来源。`upload_task.content_sha256` 与对象内容必须对得上。

权威里允许保留的密文列：`material_rag_unit.body_ciphertext`、`material_rag_scope_binding.dataset_ref_*`。它们在**不恢复旧 key**时不可读，只作“曾存在过”的痕迹，restore 后必须作废，不得拿来当可检索正文或远端 dataset 句柄。

### 派生（禁止当 restore 源，允许丢弃后重建）

- RAGFlow MySQL 卷 `material_rag_mysql_data`
- Elasticsearch 卷 `material_rag_es_data`
- RAGFlow 对象库 `material_rag_objectstore_data`
- RAGFlow cache `material_rag_cache_data`
- Redis `material_rag_redis_data`
- 远端 dataset / document / chunk（由 `index`/`rebuild` 从权威数据再写）

源码支持：`dataset_for_material_scope` 按 `f1-material-{scope.hex}` 查找或创建；`reconcile_version` / `delete_empty_scope_dataset` 以 PostgreSQL binding 为准。空 RAGFlow 加上 rebuild 队列即可补偿。

### 禁止备份 / 禁止 restore

- 任何 secret 卷：`material_rag_*_secrets`、`material_rag_control`（含 RAGFlow API key 文件）
- `f1_material_rag_key`、`f1_material_rag_manifest_key`
- Ark / provider 凭证、egress 审计原文、authorization 材料
- RAGFlow 原始卷（见上，派生物）
- 真实客户数据、headed UAT 状态、共享 `anhuan-f1` 与默认 closeout 卷
- 明文 SQL、DSN、正文、token 进证据文件

禁止恢复旧加密 key。新栈必须生成新 key。因此 restore 后旧 binding 与旧 unit 密文一律失效。

## f1_0015 / 38 表 manifest

身份摘要闭集 = `P2_P7_TABLES`（35）再追加且仅追加：

| 表 | 作用 | restore 后 |
| --- | --- | --- |
| `f1.material_rag_scope_binding` | scope→dataset 句柄（密文） | 全部视为失效，清密文字段，status 落到 `deleted` 或删除行 |
| `f1.material_rag_unit` | 加密 canonical unit | 因新 key 不可读；在 rebuild 前按 identity 删除，避免 `material_rag_unit_identity_uq` 冲突 |
| `f1.material_rag_job` | 任务租约/幂等 | `done`/`failed` 不可再领（本轮重投门已证）。restore 后不得重用旧 `job_id`；为每个仍 released 的当前版本 **新幂等键** 入队 `rebuild` |

计数摘要算法沿用 `ANHUAN_BUSINESS_COUNTS_V1`（`localctl._business_snapshot`），仅把 `table_names` 换成 38 元闭集。`local_backup` schema `anhuan-engineering-backup-v2` 的字段集合不必改；`business_table_count` 从 35 变为 38。

全库 `CREATE TABLE f1.*` 累计多于 38（基线壳表、upload_task、qa_request 等）。那些表在 `database.dump` 里，但不进入 38 计数身份。不得把 38 理解成“只有 38 张表要 dump”。

Alembic：专属 restore 后 `f1.alembic_version` 必须是 `f1_0015`。默认 closeout verify 仍只认 `f1_0014`，下一轮要走**专属** verify 入口，禁止改默认 `local_verify` 的 0014 合同。

## 专属标签与失败清理

只动同时满足以下标签的资源：

- `io.anhuan.scope=material-rag-verification`
- 当前 `LOCAL_MATERIAL_RAG_PROJECT_ID`
- 当前 `LOCAL_PARENT_PROJECT_ID`

禁止：共享 `anhuan-f1`、默认 `anhuan-closeout-*`、无标签卷、按名字前缀通配删除。

失败清理（设计，沿用现役 backup pending 语义）：

1. staging 目录名必须匹配 pending 模式，模式不符则拒绝，不 `rmtree`。
2. backup 中途失败：删 pending，恢复已 stop 的专属核心容器；C/V/N 与开工 fingerprint 对账。
3. restore 一旦开始破坏性写卷：失败则只清理**本 project-id** 的 postgres_data / minio_data 替换结果，不得碰 MySQL/ES 以外的共享资源；输出固定失败码后停止。
4. 证据目录 0700、文件 0600；无正文/凭证/SQL/DSN。

## verify-before-destructive

顺序闭集（在改任何卷之前全部通过，否则不进入破坏段）：

1. 包合同：`local_backup.verify_backup` 同类校验（schema、dump sha/size、minio tree sha/count/size、38 表计数 sha）。
2. Alembic 声明：manifest 或伴随元数据记录 `f1_head=f1_0015`（现役 v2 manifest 无此字段，下一轮允许加一个非敏感字段，或用独立 `material-rag-backup-v1` sidecar；不得把 secret 写进去）。
3. 标签与卷闭集：目标栈的 data 卷只有 `material_rag_postgres_data` 与 `material_rag_minio_data` 将被替换。
4. 源 MinIO 树非空且文件数/总大小与 manifest 一致。
5. 专属 C/V/N 与 control 目录在 stop 前可解释；共享 fingerprint 字节不变。

破坏段之后再次核对 dump 身份、MinIO 树、38 表计数。失败走上一节清理。本轮不实现、不演练这条破坏路径。

## restore 后旧 binding 失效与 rebuild 队列

约束来自已证明的生产行为，不是新假说：

- 加密 key 在 `F1_SECRETS_DIR/f1_material_rag_key`，禁止进备份。新栈新 key。
- unit INSERT trigger 要求 `session_user=f1_worker` 且 live lease 身份一致；binding/job 亦有 guard。因此 **不能** 在 API 恢复后用普通 worker 会话“批量清表”。
- `done`/`failed` 的 `job_id` 不能再 `claim_job`。补偿必须新幂等键。
- 未 release / 跨租户 / 撤销 release 必须在远端写之前拒绝。队列只含 **released + clean + preview-ready** 的当前版本。

可执行顺序（正式 runtime 仍未实现；本轮只在测试 harness 中演练第 3 步维护原语，不做破坏性真实恢复）：

1. **先**核对 backup manifest、`f1.alembic_version=f1_0015`、38 表计数摘要、PostgreSQL dump 身份、源 MinIO tree。任一失败则不进入破坏段。
2. Stop 全部业务服务（api/worker/dispatcher/web/RAGFlow）。冻结旧任务：不再 claim、不再 renew。消费者必须已停。
3. 专属离线 **bootstrap maintenance**（仅 `f0d_bootstrap`、核验 `current_user`/`session_user`、`session_replication_role=origin`、单事务）：
   - 先记录无敏计数。
   - **DELETE** 全部待恢复范围内的 `material_rag_job` 行（含 queued / retry_wait / 未过期 running / 已过期 running / done / failed）。禁止把非终态 `UPDATE` 成 `failed`：真实 `material_rag_guard_job` 会对 queued/retry_wait/running 直接改 failed 抛出 `MATERIAL_RAG_JOB_TRANSITION_INVALID`，事务必须 rollback，状态不变。
   - 同一事务再 **DELETE** `material_rag_unit`。
   - 同一事务把范围内 binding 的密文字段清空，并落到无密文 `deleted`（`error_reason` 亦空）。
   - 不新增宽 GRANT、不 `BYPASSRLS`、不 replica、不关 trigger。
   - 提交后 job、unit、live-lease、provisioning、deleted-but-secret、orphan unit 全为 0。未过期 running 也必须被 DELETE，不得留下 live lease。
4. 生成新 secret；启动 postgres + minio + API，**不要**还原 RAGFlow/MySQL/ES/cache 卷。
5. API 恢复后，仅为 released+clean+preview-ready 当前版本 `enqueue_job(..., action="rebuild", idempotency_key=新键)`。worker 走 `claim → process_claimed_demo_job → finish`；正文来自 MinIO 再解析。
6. Ark / provider 凭证 / RAGFlow 原始卷 / 任何 secret **永不备份、永不 restore**。

不得写成后台自动扫描 daemon。已知 `job_id` 重投只用于租约过期/retry 到期，不用于 restore。

## 明确不在下一轮范围

- 改默认 `f1_0014/35` backup 合同或 `local_backup.py` 字段闭集（除非加 sidecar）
- 备份或还原 Ark/provider/RAGFlow 原始卷
- 开放面向用户的正式 restore 命令（仅专属工程门 `material-rag-backup-restore-check`）
- 把 material-RAG 并进共享 `anhuan-f1`

## 现役结论

`MATERIAL_RAG_RESTORE_ABORT_CLEANUP_IMPLEMENTED / MATERIAL_RAG_RESTORE_CRASH_RECOVERY_IMPLEMENTED / TARGETED_TEST_BLOCKED / RUNTIME_REVALIDATION_PENDING / BACKEND_CHECKPOINT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`
