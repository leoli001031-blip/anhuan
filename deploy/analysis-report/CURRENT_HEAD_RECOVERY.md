# 当前候选备份与独立恢复

入口：`python -m infra.f1.analysis_report_backup`。只支持 `f0d_0006 / f1_0044`、PostgreSQL 18 同一补丁版本、当前 `F1_MATERIAL_RAG_LOCAL_INDEX=1` 候选。默认工程的 0014 备份工具及历史 material-RAG 0015/0016/0017 恢复合同保持不变。

## 备份的内容与一致性

- 完整 PostgreSQL custom archive，包括 F0 历史 schema、F1 所有表、序列、函数、触发器、索引、约束、RLS、ACL 与扩展。55 是专属迁移表单加人工修订表的保护集合数，并非整个数据库的表数。回执另记实际全库表数。
- `anhuan-f1-quarantine`、`anhuan-f1-documents`、`anhuan-f1-previews` 三个业务桶的全部已完成对象。每个对象保存 SHA-256、大小、ETag、Content-Type、用户 metadata 与原分片边界；不等长分片同样保留原 ETag。
- 四个现役业务密钥：`f1_material_rag_key`、`f1_qa_key`、`f0i_key`、`invite_signing_key`。存在 `f1_material_rag_manifest_key` 时一并保存；本地索引候选不要求凭空生成此密钥。若 F0-F 历史加密配置非空，必须另外提供原 `f0f_source_key`，并核验其已有加密验证值。保存历史数据不恢复 F1.1.1 formal 执行。
- 导出前、恢复后均逐条解密核对原生片段、人工修订、PDF 单元、dataset 引用、旧 OCR checkpoint、新 OCR cache 和历史问答；F0-I/F0-F 的配置密钥验证值另验。密钥长度正确但与已有密文不匹配，也不能得到通过回执。输出只记数量，不记录解密正文。

备份时必须停止 API、dispatcher、worker、ingestion-worker、report-worker 和 source-gateway，并停止其他写入/迁移任务。入口要求显式 `--writers-stopped`，检查没有其他数据库客户端，持有所有业务表的 SHARE 锁，用导出的同一 PostgreSQL snapshot 执行 pg_dump，并复核对象清单未变。此合同依赖维护停写，不声称在线 PostgreSQL 与 S3 跨系统原子快照。

源数据库里的缺对象、错误业务数据和待重试任务不会因备份而自动修复。维护前后使用 `OBJECT_RECONCILE.md` 的只读计划核对原有缺口。Redis 不进入备份，后台任务由已恢复数据库中的投递和任务记录重新派发；外部 RAGFlow 派生卷不在本地索引恢复合同内。

包目录必须是当前用户所有的真实 0700 目录，成员为独占、无符号链接/硬链接的 0600 文件。包包含业务数据和解密密钥，需按原件权限保管。manifest 最后落盘，缺失 manifest 的目录属于失败残留。回执中的 `manifest_sha256` 必须另行保留；恢复时不能用待验证包自己提供的新摘要代替原回执。

当前限额：最多 10000 个对象、单对象 128 MiB、对象总量 1 GiB、数据库行序列化总量 1 GiB、包及恢复 SQL 各不超过 2 GiB。版本化桶、SSE/对象锁、tags、未支持的额外响应 metadata 或无法重现的 ETag 明确拒绝。遇到这些条件需先扩展并演练相应合同。

## 一次性操作环境

从仓库根目录运行，安装现役 hash-locked Python 依赖并设置 `PYTHONPATH=src:.`。使用现役运维 secret 目录及 `F1_PG_HOST`、`F1_PG_PORT`、`F1_PG_DATABASE`；`f1_bootstrap_dsn` 必须与所选数据库完全匹配。密码只从现役 0600 文件读取。`MINIO_ENDPOINT` 指向本次对象存储，root 账号文件可由 `F1_MINIO_ROOT_USER_FILE` / `F1_MINIO_ROOT_PASSWORD_FILE` 指定。TLS endpoint 添加 `--minio-secure`。

设置 `F1_LOCAL_ENGINEERING=1`、`F1_MATERIAL_RAG_LOCAL_INDEX=1`。业务密钥默认从 `F1_SECRETS_DIR` 中按上述名称读取，支持现役 `F1_MATERIAL_RAG_KEY_FILE`、`F1_QA_KEY_FILE`、`F1_F0I_KEY_FILE`、`F1_INVITE_KEY_FILE` 和 `F1_MATERIAL_RAG_MANIFEST_KEY_FILE` 覆盖；历史 F0-F 使用 `F1_F0F_SOURCE_KEY_FILE`。

下列大写路径/容器参数均需替换为本次实测值；工具会再次比对 PostgreSQL 容器内的数据库名、集群 system identifier 和版本，不能只凭容器显示名称判断目标。

```sh
python -m infra.f1.analysis_report_backup backup \
  --package /PRIVATE_BACKUP/NEW_PACKAGE \
  --postgres-container SOURCE_POSTGRES_CONTAINER_ID \
  --output /PRIVATE_BACKUP/backup-receipt.json \
  --writers-stopped

python -m infra.f1.analysis_report_backup verify \
  --package /PRIVATE_BACKUP/NEW_PACKAGE \
  --manifest-sha256 ORIGINAL_RECEIPT_SHA256
```

`BACKUP_VERIFIED` 表示包与密钥已核对，不能替代独立恢复演练。

## 新环境恢复

1. 保留源环境，准备新的 Compose project、卷、数据库和 MinIO。只启动基础 PostgreSQL/MinIO 及其 `secret-init`、`storage-secret-init`、`storage-provisioner`；不运行 migrator、seed 或业务服务。数据库应仅含基础空 `f0d`/`public` schema，三个业务桶必须为空。新 PostgreSQL 集群必须区别于原集群。
2. 新环境使用新数据库/MinIO 登录密码。当前角色供应逻辑会创建受限登录和 NOLOGIN definer；不用源密码替换新密码。切换一次性操作环境到新目标后先生成计划。
3. 检查计划的 source/target 集群、数据库与存储身份，再将该计划交给 restore。操作入口不删除旧数据库，不覆盖已有对象，不启动应用。

```sh
python -m infra.f1.analysis_report_backup plan \
  --package /PRIVATE_BACKUP/NEW_PACKAGE \
  --manifest-sha256 ORIGINAL_RECEIPT_SHA256 \
  --postgres-container NEW_POSTGRES_CONTAINER_ID \
  --output /PRIVATE_BACKUP/restore-plan.json \
  --writers-stopped

python -m infra.f1.analysis_report_backup restore \
  --package /PRIVATE_BACKUP/NEW_PACKAGE \
  --manifest-sha256 ORIGINAL_RECEIPT_SHA256 \
  --postgres-container NEW_POSTGRES_CONTAINER_ID \
  --plan /PRIVATE_BACKUP/restore-plan.json \
  --output /PRIVATE_BACKUP/NEW_RESTORE_RESULT \
  --writers-stopped
```

全库恢复 SQL 来自 pg_restore 的 pre-data/data/post-data，统一在一个 PostgreSQL 事务中应用。扩展仅按记录的 owner 创建；不能全局使用 `--use-set-session-authorization`，因为当前 NOLOGIN definer 没有建 schema/function 权限。普通 pg_restore 又不能恢复扩展 owner，因此入口对已知 pgcrypto 创建语句做严格、唯一匹配。恢复后比较全部行的计数/哈希，以及角色属性、配置、规范化 ACL、扩展 owner 等完整目录。

`NEW_RESTORE_RESULT/restored-keys` 保存需要挂载的原业务密钥。按现役 secret-init 的分卷规则安装到对应 API/worker 卷，保持新环境各自的数据库与存储登录凭据；不把整个备份包或运维凭据挂给服务。

每个阶段写 fsync journal。失败没有 `result.json`，并记录 `runtime_start=BLOCKED`。SQL 中途错误整事务回滚；对象阶段失败可能留下已恢复的数据库和部分对象，保留回执检查并另建空目标再做恢复，不通过覆盖重试掩盖半成品。当前入口没有自动删除或灾难重放失败环境的功能。

## 恢复后启动与验收

先保留业务停机，核对密钥挂载，再执行租约检查。历史已完成报告保留的令牌回执不当作可恢复任务；其余已有租约自然到期后，原令牌失效，正常 dispatcher/worker 才能领取新令牌。检查不改任务状态，不绕过触发器。

```sh
python -m infra.f1.analysis_report_backup check-runtime \
  --package /PRIVATE_BACKUP/NEW_PACKAGE \
  --manifest-sha256 ORIGINAL_RECEIPT_SHA256 \
  --postgres-container NEW_POSTGRES_CONTAINER_ID \
  --restore-result /PRIVATE_BACKUP/NEW_RESTORE_RESULT/result.json \
  --output /PRIVATE_BACKUP/restart-check.json \
  --writers-stopped
```

`WAIT_LEASE_EXPIRY` 返回 exit 2，并给出实际到期时刻；到期后用新的 output 再检查。`RUNTIME_RESTART_READY` 只是数据与旧租约启动前检查，不是应用健康证明。随后按现役 Compose 规则启动新空 Redis 和业务服务，核对持久投递、四格式原件/预览、人工修订、检索和冻结报告，最后再切流；源环境保持停写。

统一验证入口：

```sh
python scripts/acceptance_gate.py --mode restore --evidence-dir /PRIVATE_EVIDENCE/current-restore --timeout 1200
```

该门使用独立真实 PostgreSQL、MinIO、新凭据和新 Python 进程，销毁源容器后核对四格式原件/片段、人工修订、检索、历史问答和邀请签名、冻结报告 HTML/PDF、不等长分片 ETag、加密 OCR cache、旧/新租约及正常 SQL 完成；覆盖损坏包/缺密钥/错误密钥、非空目标拒绝和真实 SQL 失败回滚。PDF 单元来自明确的历史合成 fixture、JPEG OCR 是合成响应；这里不证明真实材料 OCR 质量、完整 Compose 浏览器链、远端 CI、断电恢复或生产验收。
