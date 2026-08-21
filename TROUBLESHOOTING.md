# 本地工程排障手册

> **INTERNAL / NOT_PRODUCTION**
> 只处理当前 `localctl` 初始化的本地工程实例。不得为了排障复制 secret、修改业务数据，或操作没有通过双标签核验的 Docker 资源。

当前基线保留已通过的技术摘要，但精确顺序的治理证据尚未重放，状态为 `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION`。若本手册中任一固定错误在现役 checkout 重现，应重新打开相应技术门，不得沿用旧成功记录覆盖当前失败。

## 1. 排障原则

1. 保留终端中的固定 reason code；不要为了获得“更详细错误”打印环境变量、Compose 配置、secret 或数据库正文。
2. 所有处置仍使用 `./scripts/localctl`。禁止裸跑 `docker compose down -v`、`docker rm/volume rm`、名称通配符和任何 `prune`。
3. `localctl` 只允许处理当前 Compose project 下，同时具有 `io.anhuan.scope=engineering-closeout` 与当前 `io.anhuan.project-id` 的资源。身份不符是保护动作，不是需要绕过的检查。
4. 一个原因连续出现时不要盲目重试破坏性命令。保存 reason code、Git commit 和 `health --json` 聚合结果，修复代码或宿主依赖后再试。

## 2. 初始化、锁与宿主环境

| reason code / 现象 | 含义 | 安全处置 |
|---|---|---|
| `LOCAL_NOT_INITIALIZED` | `health/stop/backup/restore/verify/reset` 找不到当前实例状态 | 若是全新工作区，运行一次 `./scripts/localctl start`。若原本有数据，不要新造 `.local`；先确认是否位于正确 checkout。 |
| `LOCAL_COMMAND_ALREADY_RUNNING` | 另一个 `localctl` 持有排他锁 | 等原命令结束再重试。锁文件存在不代表锁仍被占用，不要删除 `command.lock`。 |
| `LOCAL_DOCKER_UNAVAILABLE` | 找不到受支持的 Docker CLI/daemon | 启动本机 Docker Desktop，确认当前用户可用后重试。不要改 `DOCKER_HOST` 指向远端 daemon。 |
| `LOCAL_DOCKER_UNTRUSTED` / `LOCAL_COMPOSE_PLUGIN_UNTRUSTED` | Docker 或 Compose plugin 的文件身份/权限不可信 | 恢复官方本机安装；不要把临时脚本伪装成 docker/compose。 |
| `LOCAL_COMMAND_FAILED` | 受控外部命令返回失败或超时 | 不继续 restore/reset。先运行 `health --json`；修复 Docker 磁盘、内存或镜像可用性后，从同一 `localctl` 子命令重试。 |

## 3. 状态、权限与资源身份

| reason code | 安全处置 |
|---|---|
| `LOCAL_STATE_INVALID` | 不要编辑或复制其他工作区的 `state.json`。保留 `.local` 原状，确认 checkout 与操作系统用户；在身份问题解决前停止所有破坏性命令。 |
| `LOCAL_DIRECTORY_PERMISSIONS_INVALID` / `LOCAL_FILE_PERMISSIONS_INVALID` / `LOCAL_FILE_MISSING` | 不要用 `cat/cp/chmod -R` 修补 secret 集合。确认没有同步盘、软链接、硬链接或其他用户接管 `.local`；将其视为安全故障。 |
| `LOCAL_SECRET_SET_INCOMPLETE` | secret 集合部分缺失，不能自动补齐 | 不从其他实例复制缺项，不重新生成单个 secret。保留现场并通过受审查的工程变更重建整套实例。 |
| `LOCAL_COMPOSE_ENV_DRIFT` / `LOCAL_DOCKER_CONFIG_DRIFT` | 受管控制文件被修改 | 不手工对照 secret 或覆盖文件；恢复生成这些文件的代码/checkout 一致性后重试。 |
| `LOCAL_RESOURCE_IDENTITY_INVALID` / `LOCAL_RESOURCE_IDENTITY_MISMATCH` / `LOCAL_DATA_VOLUME_IDENTITY_INVALID` | 资源选择结果缺失双标签、标签冲突或数据卷身份不唯一 | 立即停止。不要改标签、按名称删除或把资源并入当前项目；先查明是谁创建了冲突资源。 |
| `LOCAL_REVERSE_RECOVERY_PENDING` / `LOCAL_REVERSE_RECOVERY_INVALID` / `LOCAL_REVERSE_RECOVERY_IDENTITY_MISMATCH` | 上次受控依赖停机或 foreign sentinel 演练尚未完成，或恢复 journal 与当前项目身份不符 | 保留 0600 journal，不手工删除或改写。修复 Docker 可用性或 checkout/state 漂移后重跑任一 `localctl` 命令；它会先按精确 ID/nonce 恢复。身份不符时停止并保留现场。 |
| `LOCAL_REVERSE_DEPENDENCY_RECOVERY_FAILED` / `LOCAL_REVERSE_SENTINEL_CLEANUP_FAILED` | 精确依赖容器或外来 sentinel 未能恢复/清理 | 不运行 daemon 级 prune、名称通配符或裸 `docker rm`。保留固定 reason 与 journal，修复 Docker 后重试同一 `localctl` 命令。 |

## 4. 健康、迁移、种子与校验

### `LOCAL_HEALTH_RED`

运行：

```bash
./scripts/localctl health --json
```

只依据聚合布尔值判断层次：

- `containers=false`：至少一个核心服务缺失、重复、未运行或不健康。确认 Docker Desktop 资源充足后运行 `./scripts/localctl start` 收敛状态。
- `containers=true` 但 API/OIDC/Web 某项为 `false`：先 `stop`，再 `start`，随后重新执行 `verify` 与 `health --json`。
- 重启后仍红：停止操作并保留 reason code。不要直接进入容器改配置，也不要把 HTTP 200 当成容器健康的替代。

### migration 或 seed 失败

`migrate` 和 `seed` 设计为可重放，但失败不等于可以手工改 Alembic version table 或删除行。修复明确的代码/依赖问题后重新运行原命令；成功后必须运行 `verify`。禁止 `stamp`、手改 `f0d.alembic_version/f1.alembic_version` 或用 migration 角色启动 API/Worker。

### `verify` 失败

| reason code | 含义与处置 |
|---|---|
| `LOCAL_VERIFY_CONNECTION_FAILED` | 数据库不可用或受管连接失败；先用 `health --json` 定位，再 `start`。 |
| `LOCAL_VERIFY_DATABASE_IDENTITY_MISMATCH` | 当前数据库/用户不是本实例 bootstrap 身份；停止，禁止切 DSN 绕过。 |
| `LOCAL_VERIFY_HEAD_MISMATCH` | F0/F1 head 不符合冻结合同；执行 `migrate` 后再 `verify`，仍失败则停止。 |
| `LOCAL_VERIFY_RLS_MISMATCH` | FORCE RLS 表集合不符；这是安全故障，禁止关闭 RLS 让检查变绿。 |
| `LOCAL_VERIFY_RUNTIME_ROLE_MISMATCH` / `LOCAL_VERIFY_ROLE_MEMBERSHIP_MISMATCH` | API/Worker 低权限角色或成员关系漂移；禁止授予超级用户、migration role 或 BYPASSRLS。 |
| `LOCAL_VERIFY_SEED_ENTERPRISE_MISMATCH` / `LOCAL_VERIFY_SEED_BINDING_MISMATCH` | 固定合成种子不一致；运行 `seed` 后重验，禁止直接改行凑计数。 |

`verify` 的完整成功边界是五个固定标签同时出现：`LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK`、`LOCAL_LOG_VERIFY_OK`。只出现前面部分标签不等于通过。

## 5. 日志、secret 与 PII 边界失败

| reason code | 含义与安全处置 |
|---|---|
| `LOCAL_LOG_BOUNDARY_FAILED` | 聚合扫描发现 secret、DSN、令牌、PII、文件名、正文标记或大小边界不为 0。停止收口，不打印原始日志或文件；只根据固定代码审查最近变更。 |
| `LOCAL_LOG_CONTAINER_SET_INVALID` / `LOCAL_LOG_CONTAINER_IDENTITY_INVALID` | 9 个核心服务集合或双标签身份不可信；先用 `health --json` 和 `start` 收敛，不按名称抓取其他容器日志。 |
| `LOCAL_LOG_READ_FAILED` / `LOCAL_LOG_READ_TIMEOUT` / `LOCAL_LOG_SIZE_LIMIT` | 无法在有界条件下完整读取日志；不得改用无界 `docker logs` 或把原文保存到排障记录。修复日志规模或 Docker 状态后重跑 `verify`。 |
| `LOCAL_LOG_REPOSITORY_SCAN_INVALID` / `LOCAL_LOG_BACKUP_SCAN_INVALID` / `LOCAL_LOG_FILE_SCAN_CHANGED` / `LOCAL_LOG_FILE_SCAN_SIZE_LIMIT` | 仓库或最新有效备份无法稳定、安全地扫描；停止，不绕过权限、软链接、身份或大小门。 |

## 6. `browser-verify` 与 PWA 更新失败

| reason code / 状态 | 含义与安全处置 |
|---|---|
| `LOCAL_BROWSER_*` | 浏览器启动、输出合同、超时或固定失败原因异常；先确认 `health` 全绿，再重跑。不要打印 token、浏览器 stderr 或会话存储。 |
| `LOCAL_BROWSER_RECOVERY_*` / `LOCAL_BROWSER_PROCESS_*` / `LOCAL_BROWSER_PROFILE_*` | 上一次浏览器验收被中断，或恢复日志、进程组、profile 身份不可信。不要删 journal、不要 `pkill`、不要按目录前缀清理；保持 `.local/browser-recovery.json`，修复明确的权限或 Docker 状态后重跑任一 `localctl` 命令触发精确恢复。 |
| `MINIO_FAULT_*` / `CLAMD_FAULT_*` | 真实故障握手、503/重试或扫描可用性 UI 不符合合同 | `browser-verify` 会在 finally 先按 recovery journal 恢复精确服务。先运行 `health --json`；若仍红，重跑任一 `localctl` 触发恢复，再重新执行完整 browser-verify。不要 mock 响应或手工改页面状态。 |
| `LOCAL_PWA_*` | A→B 临时镜像、控制目录、更新信号或 A 版恢复身份异常；停止并用 `start`/`health` 收敛。不手工删 cache、镜像或控制目录扩大范围。 |
| `PWA_OS_INSTALL_NOT_TESTED` | 这是明确状态，不是失败 reason code。当前命令没有执行 OS 级应用安装，状态保持 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`，不得改写为通过。 |

## 7. 备份、恢复和清理错误

| reason code | 安全处置 |
|---|---|
| `LOCAL_BACKUP_SOURCE_UNAVAILABLE` | 核心服务未全部健康；先 `start`、`verify`、`health`，再备份。 |
| `LOCAL_BACKUP_NOT_FOUND` / `LOCAL_BACKUP_ID_INVALID` | 指定 ID 不存在或格式非法；从成功时记录的 `LOCAL_BACKUP_OK <id>` 选择，不改目录名。 |
| `LOCAL_BACKUP_CATALOG_INVALID` | `.local/backups` 出现额外、错误权限或非目录条目；停止，不移动或打包目录。 |
| `LOCAL_BACKUP_MANIFEST_INVALID` | manifest、数据库 dump 或 MinIO 树不完整/被修改/不属于本实例；该备份不可恢复，禁止跳过校验。 |
| `LOCAL_BACKUP_RESUME_FAILED` | 备份后服务未恢复；运行 `health --json`，再用 `start` 收敛。此次 pending 备份不应当作成功备份。 |
| `LOCAL_RESTORE_CONFIRMATION_REQUIRED` / `LOCAL_RESET_CONFIRMATION_REQUIRED` | 缺少显式数据确认；先确认操作目标，再使用文档规定的 `--confirm-local-data`。 |
| `LOCAL_RESTORE_DATA_VOLUME_RESIDUAL` / `LOCAL_RESTORE_DATA_VOLUME_INCOMPLETE` / `LOCAL_RESTORE_CLEANUP_FAILED` | 恢复过程中数据卷未达到原子边界；停止，不手工创建、挂载或删除卷。按 [RECOVERY.md](RECOVERY.md) 的失败流程处理。 |
| `LOCAL_RESET_RESIDUALS` / `LOCAL_STOP_RESIDUALS` | 当前双标签项目仍有残余；不要扩大到 daemon 级清理，保留现场并修复 identity/Compose 问题。 |

## 8. 专属 material-RAG 验证失败

当前阻塞分类是 `LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED`，权威说明见 [MATERIAL_RAG_BLOCKED.md](./MATERIAL_RAG_BLOCKED.md)。历史 `LOCAL_MATERIAL_RAG_ARK_KEY_INVALID` 与折叠后的 `LOCAL_COMMAND_FAILED` 不是现役原因。

不要为排障重跑 `./scripts/localctl material-rag-verify`，也不要向 Ark 发送 allowlist 文本，除非用户给出新的字面授权。默认 `localctl migrate/start/verify` 不是本切片入口；本树默认 migrate 还会因 `upgrade head` 与 `f1_0014` 校验冲突而失败回滚。失败只保留固定 reason 和聚合计数，不粘贴 RAGFlow 日志、正文、原名、dataset ID 或凭据。

## 9. 禁止进入排障记录的内容

不要粘贴 `.local/secrets/*`、`.local/compose.env`、完整 `state.json`、容器环境、容器日志原文、浏览器 stderr、DSN、Authorization header、OIDC token、用户邮箱、文件名或文件正文。允许记录的只有固定 reason code、退出码、Git commit、backup-id，以及 `health --json`/`verify`/`browser-verify` 的聚合计数、布尔值或固定状态。
