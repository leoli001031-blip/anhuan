# 本地工程备份与恢复手册

> **INTERNAL / NOT_PRODUCTION**
> 这是同一台本机、同一个工程实例内的恢复机制，不是异机迁移、长期归档或生产灾备。备份不包含 secret，也不得通过复制 secret 把备份移植到新实例。

## 1. 备份合同

运行：

```bash
./scripts/localctl backup
```

前提是当前实例已初始化且九个核心容器全部 `running/healthy`。命令会：

1. 核验当前 Compose project 中每个资源的 `io.anhuan.scope=engineering-closeout` 与当前 `io.anhuan.project-id` 双标签；
2. 短暂停止 `web/api/worker/dispatcher/minio`，避免应用写入和对象树变化；
3. 在 PostgreSQL 仍可用时生成 custom-format dump，并复制停止状态下的 MinIO 数据；
4. 生成并复核只含聚合信息的 canonical manifest；
5. 恢复被停止的核心服务；只有恢复成功后，pending 目录才会原子改名为正式备份。

成功输出：

```text
LOCAL_BACKUP_OK <backup-id>
```

`backup-id` 格式为 UTC 时间加随机后缀，例如 `YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx`。将 ID 记入非敏感维护记录，不要改备份目录名。

正式备份位于 `.local/backups/<backup-id>/`，且只允许三个根条目：

- `database.dump`：PostgreSQL custom-format dump；
- `minio-data/`：MinIO 对象树；
- `manifest.json`：绑定 schema、当前 project-id、数据库名、数据库摘要/大小和 MinIO 树摘要/计数的聚合 manifest。

这些文件虽不含 local secret，仍可能包含内部数据，目录为私有 `0700`、文件为 `0600`。不要打开正文、手改、压缩、同步、上传或提交。manifest 故意不列对象路径，但其树摘要绑定每个目录名、文件名、大小和内容摘要。

## 2. 恢复前决策

恢复会替换当前实例的 PostgreSQL 与 MinIO 数据。Keycloak、Redis 和其他非数据卷不是此备份合同的一部分；恢复完成后 `start` 流程会重新迁移、seed 和 provision。

开始前确认：

- 当前 checkout 就是创建该 `.local` 的工作区；
- 当前 Git commit 与要恢复的数据合同兼容；
- 没有另一个 `localctl` 命令在运行；
- backup-id 来自一次成功的 `LOCAL_BACKUP_OK`；
- 当前数据允许被替换。

备份被绑定到当前 `project_id` 和数据库名。若 `.local/state.json` 丢失、重建或来自其他 checkout，即使目录内容看似完整也不能跨实例恢复；不要复制旧 secret 或修改 manifest/state 解除绑定。

## 3. 执行恢复

恢复最新有效备份：

```bash
./scripts/localctl restore --confirm-local-data
```

恢复指定备份：

```bash
./scripts/localctl restore --backup-id <backup-id> --confirm-local-data
```

不要省略 `--confirm-local-data`，也不要把选项写到子命令之前。`--backup-id` 只能使用完整 ID；省略时由 `localctl` 从当前私有 catalog 选择最新正式备份，`.pending-*` 永远不会被选中。

恢复过程先在破坏性操作前验证目录权限、精确条目、manifest canonical 形式、project/database 绑定、database dump 摘要和 MinIO 树摘要。通过后才会：

1. 记录当前项目的 `postgres_data` 与 `minio_data` 精确卷身份；
2. 停止并移除当前双标签项目容器；
3. 只删除已核验的两个数据卷；
4. 新建 PostgreSQL 数据卷，先由原子 migrator 预置 dump 所引用的受保护角色，再以单事务恢复 dump；
5. 新建 MinIO 数据卷并恢复对象树；
6. 执行标准 `start` 收敛 migration、seed、Keycloak 和全部核心服务。

成功输出 `LOCAL_RESTORE_OK <backup-id>`。随后立即执行：

```bash
./scripts/localctl verify
./scripts/localctl health --json
```

`verify` 必须退出 0，并同时包含 `LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK` 和 `LOCAL_LOG_VERIFY_OK`；health 必须全部为 true。日志门会在恢复后重新检查 9 个核心服务、仓库文件以及最新有效备份，确认没有当前 secret、DSN、令牌、PII、文件名或正文标记泄漏。不要用能打开首页或单个 HTTP 200 替代这两个判据。

如果本次是完整工程 checkpoint，或恢复涉及前端、OIDC、PWA 变更，随后另外运行：

```bash
./scripts/localctl browser-verify
```

它必须认证 3 类身份，访问管理员 17、顾问 2、企业 2 个角色页面，并输出 `LOCAL_BROWSER_VERIFY_OK` 与 `PWA_WAITING_UPDATE_PASSED`。该命令不验证 OS 级应用安装；后者仍为 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`。

## 4. 失败后的处置

### manifest 或 catalog 在破坏前失败

若返回 `LOCAL_BACKUP_MANIFEST_INVALID`、`LOCAL_BACKUP_CATALOG_INVALID`、`LOCAL_BACKUP_NOT_FOUND` 或 `LOCAL_BACKUP_ID_INVALID`，原数据卷尚不应被替换。停止操作，不要改 manifest、目录名、权限、摘要或文件树；选择另一份已成功记录的备份，或先创建一份新的有效备份。

### 破坏性阶段开始后失败

`localctl` 会 fail closed：尝试移除当前项目的部分容器和 PostgreSQL/MinIO 数据卷，避免一个半恢复的应用继续运行。此时：

1. 不要运行裸 Docker 命令，不要手工挂载或删除卷；
2. 不要先运行 `start` 创建空数据并掩盖故障；
3. 保留固定 reason code 与所选 backup-id；
4. 修复明确的 Docker 磁盘、内存或受管代码问题后，使用同一个有效 backup-id 重新执行完整 `restore ... --confirm-local-data`；
5. 再以 `verify` 和 `health --json` 收口。

如果出现 `LOCAL_RESTORE_CLEANUP_FAILED`，说明当前项目无法证明已回到安全空边界。禁止扩大删除范围或绕过双标签核验；在资源身份问题解决前保持停机。

## 5. reset 后恢复

`./scripts/localctl reset --confirm-local-data` 会删除当前双标签项目的全部容器、卷和网络，但保留 `.local` 的 state、secret 与 backups。因此在 reset 明确成功后，可以用上述 `restore` 命令恢复 PostgreSQL/MinIO，再执行统一五门 `verify` 与 `health`。

若只需验证“重启不丢数据”而不替换数据卷，使用 `stop → start → health --json → verify`。`start` 会强制重建精确 9 个核心容器但保留卷；这条链已实际通过，不得与删卷后的 backup/restore 演练混为同一证据。

如果 `.local` 本身被删除或 secret 集合不完整，当前备份合同不能自行恢复身份和凭据。不要从别处复制 secret；这属于重新建立工程实例，而不是本手册覆盖的恢复操作。

## 6. 恢复演练记录

每次恢复只记录：Git commit、所选 backup-id、restore 退出码/固定 reason code、五门 `verify` 聚合计数与 `LOCAL_LOG_VERIFY_OK`、`health --json` 聚合布尔值，以及需要时的 `LOCAL_BROWSER_VERIFY_OK` / `PWA_OS_INSTALL_NOT_TESTED`。不要记录 dump 内容、MinIO 路径、manifest 原文、数据库行、日志原文、secret 或用户资料。


## 7. 2026-08-11 最终恢复演练证据

- 定向检查：`230/230 OK`。
- 备份 ID：`20260810T224332Z-2a861bccbba9`。
- 完整执行 `reset → restore`，恢复成功。
- 恢复后 health ready，统一 verify 五门全绿。
- 恢复后 browser-verify 通过：3 类身份，管理员 17 页、顾问 2 页、企业 2 页，管理员 API 92 次且非 2xx 为 0，`PWA_WAITING_UPDATE_PASSED`。
- OS 级 PWA 安装仍为 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`；PDF Inspector 仍为 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`。
- 最终状态：`INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
