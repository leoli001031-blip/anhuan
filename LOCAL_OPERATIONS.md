# 本地工程运行手册

> **INTERNAL / NOT_PRODUCTION**
> 仅供单名工程维护者在本机使用。它不是客户环境、生产部署方案、专业准确性证明或灾备系统。

## 1. 唯一入口与安全边界

所有生命周期操作都从仓库根目录通过 `./scripts/localctl` 执行。不要绕过它直接运行 `docker compose`、按名称或前缀操作容器/卷/网络，也不要执行任何 daemon 级 `prune`。

当前维护基线是仓库根目录的 `codex/engineering-closeout` checkout。旧 repair worktree 只读保留，不得从那里执行本手册命令。

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

先运行固定范围的直接检查：

```bash
./scripts/localctl test
```

它在与服务相同的固定 Python 镜像中运行仓内冻结的 21 个 P2–P7/工程收口模块，并真实构建固定 Node/Web/P8 镜像；不依赖宿主 `.venv` 或 `node_modules`。成功只输出聚合计数与 `LOCAL_TARGETED_TESTS_OK`；必须 `tests>=137`、`web_builds=1` 且 `errors=failures=skipped=0`。P8 的运行态再由下方真实浏览器门覆盖。

再运行真实依赖与随机数据库合同：

```bash
./scripts/localctl verify
```

`verify` 是无正文输出、无持久业务写入的工程校验，现在同时覆盖五个门：

1. 数据库身份、`f0d_0006/f1_0014` 双 head、35 张 P2–P7及材料录入表 ENABLE + FORCE RLS、低权限运行角色和固定合成身份；
2. 独立随机 scratch 数据库中的迁移失败原子性，验证后精确删除；
3. P2/P4–P7 真实 API + RLS，包括非法关闭 409 后业务/audit/timeline/notification 零漂移，以及应用 engine/factory 重建后仍能读取 5 类关键业务；
4. P3 真实 ClamAV 扫描、预览和 release，以及 MinIO 写失败、ClamAV 不可用后的幂等恢复；
5. 9 个核心服务的完整有界日志、Git 跟踪及未忽略文件、以及如存在时的最新有效备份中的 secret、DSN、令牌、PII、文件名和正文标记边界。

成功必须依次包含 `LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK` 和 `LOCAL_LOG_VERIFY_OK`。所有输出只有聚合计数和固定标签。失败时按 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 处理，不要改数据库“对齐数字”，也不要打印日志或手工清理未通过身份核验的资源。

现役备份清单记录 35 表；历史 31/34 表工程备份在恢复时先移除后续版本新增的空 schema，再恢复旧 dump，随后由 migrator 升到当前 `f1_0014`。该兼容路径已写入合同，但本知识归属增量尚未执行真实恢复验证。

### 验证材料录入双知识域

材料录入迁移或知识域权限发生变化后，运行窄正常验证：

```bash
./scripts/localctl migrate
./scripts/localctl material-verify
./scripts/localctl stop
```

`material-verify` 只使用专属 PostgreSQL、MinIO、ClamAV，并把同一份无客户数据的确定性合成文本 PDF 在服务公司域和一个合成 CRM 客户域各上传一次。它验证扫描、安全预览、分析、释放、知识域 RLS、服务公司政策草稿，以及客户材料在 API 和数据库 trigger 两层均不能进入公司政策库；不启动浏览器，不使用真实 Demo 或客户材料，也不建立物理 RAG 索引。

2026-08-12 的正常验证中，`migrate` 从实库 `f1_0011` 执行到 `f1_0013`。第一次运行因旧文档回填受 `FORCE RLS` 遮蔽而失败，Alembic 事务整体回滚；回填改为只在验证过的 bootstrap session 中执行有界 `RESET ROLE` 后，完整重跑输出 `LOCAL_MIGRATE_OK`。提交前增加底层原件/任务 scope 限制后，再从 `f1_0013` 迁移到 `f1_0014` 并得到同一固定成功结果。

最终 `LOCAL_MATERIAL_VERIFY_OK` 的精确聚合结果为：version=2、clean=2、preview=2、released object=2、analysis=2、page=2、candidate=8、scope=2（服务公司 1、客户 1）、客户负责人文档可见=1、底层原件/任务可见=2、同租户非负责人可见=0、跨租户可见=0、客户材料 API 政策拒绝=1、数据库政策拒绝=1、服务公司 policy draft=1、publication=0。scratch 数据库、临时对象和随机桶残留均为 0；最后 `stop` 输出 `LOCAL_STOPPED`。

本命令通过只表示 `SMOKE_PASSED / NOT_PRODUCTION`。它不覆盖真实 Demo PDF、浏览器、OCR、物理 RAG、候选准确率、备份恢复、发布验收或生产。

### 反向依赖与清理边界

```bash
./scripts/localctl dependency-verify
```

该命令不会修改现役 secret。它在私有临时目录生成结构相同的 synthetic secret-set，证明任一 0644 文件会被拒绝；随后按当前项目、服务、scope、project-id 和精确容器 ID 依次停止 MinIO 与 ClamD，要求 Docker 健康门和 `/api/readyz` 同时变红，再恢复到全绿。每次停止前先写 0600 恢复 journal；即使进程异常退出，下一个 `localctl` 命令也会先按原身份恢复，身份漂移则拒绝操作。

完整 checkpoint 的第一次 reset 可额外证明外来资源不被误删：

```bash
./scripts/localctl reset --confirm-local-data --prove-foreign-sentinel
```

它使用现有固定镜像创建一个随机、无网络、未启动且不带当前项目标签的 container+volume sentinel，再执行当前项目 reset。sentinel 必须存活，随后按 nonce、image、双 label 与 mount 精确清理；失败或中断仍保留 recovery journal 供下一命令收口。该命令和普通 reset 一样会删除当前项目数据卷，只能用于已确认的工程演练。

### 验证真实浏览器与 PWA 更新

```bash
./scripts/localctl browser-verify --stage business
./scripts/localctl browser-verify --stage faults
./scripts/localctl browser-verify --stage pwa-update
```

三个分段命令分别验证真实 Keycloak 角色与权限、MinIO/ClamD 故障链、PWA waiting update。这样调试单段时不再从身份、故障链一路重跑到 PWA。默认不带 `--stage` 的 `browser-verify` 仍用于最终总链收口，但不会执行 macOS OS 安装。

命令开始后会先写 0600 的 `.local/browser-recovery.json`。正常失败保留首个业务/浏览器 reason 并完成全部收尾；进程被中断时，下一条 `localctl` 命令会先按 project/probe 和进程组身份恢复 A、清理 B 与本轮私有 profile/canary/control，再进入所请求的操作。不要删除该 journal 或手工清理前缀相似的进程、镜像和目录。

PWA 更新分段成功输出 `LOCAL_PWA_UPDATE_VERIFY_OK` 和 `PWA_WAITING_UPDATE_PASSED`。`browser-verify --stage pwa-os` 当前会固定拒绝，不启动浏览器；OS 安装、在线启动、停站离线重开和卸载保持 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`。它不是日常启动命令，只在前端、OIDC、PWA 或工程 checkpoint 收口时运行。

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
./scripts/localctl test
./scripts/localctl reset --confirm-local-data
./scripts/localctl start
./scripts/localctl migrate
./scripts/localctl migrate
./scripts/localctl seed
./scripts/localctl health --json
./scripts/localctl verify
./scripts/localctl dependency-verify
./scripts/localctl stop
./scripts/localctl start
./scripts/localctl health --json
./scripts/localctl verify
./scripts/localctl backup
./scripts/localctl reset --confirm-local-data --prove-foreign-sentinel
./scripts/localctl restore --backup-id <backup-id> --confirm-local-data
./scripts/localctl health --json
./scripts/localctl verify
./scripts/localctl browser-verify
./scripts/localctl health --json
./scripts/localctl stop
```

任一步失败都会重新打开工程完成门。旧记录保留的技术摘要为：定向检查 `230/230 OK`，备份 `20260810T224332Z-2a861bccbba9` 完成 `reset → restore`，恢复后 health ready、verify 五门全绿、browser-verify 通过。由于精确顺序的治理证据表尚未重放填写，当前状态为 `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION`。
