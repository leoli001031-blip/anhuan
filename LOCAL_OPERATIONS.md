# 本地工程运行手册

> **INTERNAL / NOT_PRODUCTION**
> 仅供单名工程维护者在本机使用。它不是客户环境、生产部署方案、专业准确性证明或灾备系统。

## 1. 唯一入口与安全边界

所有生命周期操作都从仓库根目录通过 `./scripts/localctl` 执行。不要绕过它直接运行 `docker compose`、按名称或前缀操作容器/卷/网络，也不要执行任何 daemon 级 `prune`。

当前维护基线是 `/private/tmp/anhuan-codex-engineering-closeout` 的 `codex/engineering-closeout`。旧 repair worktree 只读保留，不得从那里执行本手册命令。

`localctl` 在 `.local/state.json` 中保存本次本地工程实例的身份。它只枚举 `com.docker.compose.project` 等于该实例项目名的资源，并逐个核验以下双标签：

- `io.anhuan.scope=engineering-closeout`
- `io.anhuan.project-id=<当前 state 中的 project_id>`

任一标签缺失或不一致都会拒绝操作。不要手工补标签、改 `state.json`，也不要把另一个 Compose 项目的资源改名后并入。

本地状态目录 `.local/` 只属于当前操作系统用户：目录应为 `0700`，控制文件和 secret 应为 `0600`。禁止读取、打印、复制、提交、打包或发送 `.local/secrets/` 中的内容；排障记录也不得包含 DSN、令牌、密码、用户资料或业务正文。

## 2. 日常操作

标准单人维护顺序如下。一次只运行一条命令；并发命令会被进程锁拒绝。

### 启动或收敛到已启动状态

```bash
./scripts/localctl start
```

首次运行会创建隔离的 `.local` 状态与 secret、分配仅绑定 `127.0.0.1` 的随机 Web 端口，随后构建本地镜像、启动依赖、迁移、写入合成种子数据、配置 Keycloak，并启动 API、Worker、Dispatcher 和 Web。成功末行形如 `LOCAL_READY http://127.0.0.1:<port>`。

此环境关闭外部 pipeline；成功启动不代表任意文件导入、专业判断或生产可用。

### 查看健康状态

```bash
./scripts/localctl health
./scripts/localctl health --json
```

普通输出只给就绪地址。JSON 输出只包含聚合布尔值：核心容器、API 存活、API 就绪、OIDC discovery 和 Web。九个核心服务必须恰好存在且均为 `running/healthy`：`postgres`、`keycloak`、`minio`、`redis`、`clamd`、`api`、`worker`、`dispatcher`、`web`。

### 校验工程合同

```bash
./scripts/localctl verify
```

`verify` 是无正文输出、无持久业务写入的工程校验，现在同时覆盖五个门：

1. 数据库身份、`f0d_0006/f1_0010` 双 head、31 张 P2–P7 表 ENABLE + FORCE RLS、低权限运行角色和固定合成身份；
2. 独立随机 scratch 数据库中的迁移失败原子性，验证后精确删除；
3. P2/P4–P7 真实 API + RLS，包括非法关闭 409 后业务/audit/timeline/notification 零漂移，以及应用 engine/factory 重建后仍能读取 5 类关键业务；
4. P3 真实 ClamAV 扫描、预览和 release，以及 MinIO 写失败、ClamAV 不可用后的幂等恢复；
5. 9 个核心服务的完整有界日志、Git 跟踪及未忽略文件、以及如存在时的最新有效备份中的 secret、DSN、令牌、PII、文件名和正文标记边界。

成功必须依次包含 `LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK` 和 `LOCAL_LOG_VERIFY_OK`。所有输出只有聚合计数和固定标签。失败时按 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 处理，不要改数据库“对齐数字”，也不要打印日志或手工清理未通过身份核验的资源。

### 验证真实浏览器与 PWA 更新

```bash
./scripts/localctl browser-verify
```

`browser-verify` 使用真实 Keycloak 身份访问本地页面和 API：认证 3 类身份，访问管理员 17、顾问 2、企业 2 个角色页面，核对租户切换清空旧状态、Service Worker、应用 cache、敏感数据不缓存和离线静态壳。随后它构建临时 B 版 Web 镜像，在同一 origin 上通过页面的“检查更新”与“应用更新”操作完成 waiting update，再恢复 A 版并删除精确 B 镜像和私有控制目录。

成功输出 `LOCAL_BROWSER_VERIFY_OK` 和 `PWA_WAITING_UPDATE_PASSED`。该命令不验证操作系统级应用安装；`PWA_OS_INSTALL_NOT_TESTED` 对应 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`，不是安装成功。它不是日常启动命令，只在前端、OIDC、PWA 或工程 checkpoint 收口时运行。

### 迁移与种子数据维护

```bash
./scripts/localctl migrate
./scripts/localctl seed
```

`migrate` 只准备 migration 所需组件并执行当前迁移；`seed` 会先执行 migration，再幂等写入固定合成数据。这两条不是完整启动命令。正常开机仍使用 `start`，维护后使用 `verify`。

### 停止但保留数据

```bash
./scripts/localctl stop
```

该命令停止当前实例所有已核验容器，保留卷、网络、本地状态、secret 和备份。停机后 `health` 返回非零是预期结果。

随后再运行 `start` 会强制重建精确 9 个核心容器，但保留 PostgreSQL、MinIO 等受管卷。本链已实际验证数据重启后仍存在；每次执行后仍必须用 `health --json` 和 `verify` 当场收口。

### 创建备份

```bash
./scripts/localctl backup
```

备份要求九个核心服务全部健康。命令短暂停止写入相关服务，备份 PostgreSQL 与 MinIO，校验私有 manifest，再恢复服务。成功输出 `LOCAL_BACKUP_OK <backup-id>`。详细范围与恢复步骤见 [RECOVERY.md](RECOVERY.md)。

### 恢复备份

```bash
./scripts/localctl restore --confirm-local-data
./scripts/localctl restore --backup-id <backup-id> --confirm-local-data
```

省略 `--backup-id` 时选择当前实例最新的有效备份。恢复会替换当前 PostgreSQL/MinIO 数据，必须显式确认；执行前先完整阅读 [RECOVERY.md](RECOVERY.md)。

### 清空当前项目资源

```bash
./scripts/localctl reset --confirm-local-data
```

这是破坏性操作：删除当前双标签项目的容器、卷和网络。它不会使用通配符或 daemon 级清理，也不会删除 `.local` 中的身份、secret 或备份。若目标只是暂停，使用 `stop`。

## 3. 每次维护的收口记录

只记录以下非敏感信息：日期、Git commit、执行的 `localctl` 子命令、退出码、固定 reason code、`health --json` 的布尔结果、verify/browser-verify 的聚合计数和固定状态、backup-id。不要记录 `.local` 绝对路径之外的内部路径，不要粘贴容器环境、数据库行、HTTP body、日志原文或 secret 文件内容。

建议的停机维护闭环是：先 `health`，再 `backup`，完成维护后 `start`、`verify`、`health --json`。任一步非零即停止后续破坏性动作，转到排障手册。

工程 checkpoint 的完整收口序列是：

```bash
./scripts/localctl health --json
./scripts/localctl verify
./scripts/localctl browser-verify
./scripts/localctl stop
./scripts/localctl start
./scripts/localctl health --json
./scripts/localctl verify
```

任一步失败都会重新打开工程完成门。最新一次完整收口已通过：定向检查 `230/230 OK`；备份 `20260810T224332Z-2a861bccbba9` 完成 `reset → restore`；恢复后 health ready、verify 五门全绿、browser-verify 通过。当前状态为 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
