# Engineering Closeout Progress

> **后续产品切片（2026-08-12）：** `MATERIAL_INTAKE` 已在独立 `codex/material-intake` 分支继续演进；人工类型分流及公司／客户知识归属已完成专属实库迁移和双知识域合成 PDF smoke，状态为 `SMOKE_PASSED / NOT_PRODUCTION`，精确现役 head 与边界以 `PROJECT_STATUS.md` 为准。下文 `f1_0010`、31 表与实跑计数均是 `codex/engineering-closeout` 当时的历史证据。

## 2026-08-11 任务0开工回执

- 目标：单人可启动、维护、恢复并用真实浏览器操作 P2–P8，最终仅 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
- 顺序：安全分支 → localctl/独立栈 → 数据库/RLS/后端 → OIDC/前端/PWA → 备份恢复 → 最终工程门。
- 基线：fresh clone `origin/main@8d2e791`，单根提交、564 文件，tree=`2070ced3fce8b0763dd6c8a2419414b92a702be2`。
- 开工时分支：`codex/engineering-closeout`；未引入旧 repair/PDF Probe 历史，当时未 push、未部署。
- 当时 F1 唯一源码 head 为 `f1_0010`。
- 开工时 Compose 有 18 服务/9 卷但无 PostgreSQL；API/worker 依赖外部数据库，缺统一运行和恢复入口。
- 本轮实跑 P3–P8 为 58/58 OK；P2–P8 合计 137 项中 4 项因旧测试硬编码 `f1_0005` 失败。
- 前端 lint/build exit 0；保留 2 个 warning 和约 1.48 MiB 单包技术债，不作为首个运行底座阻断。
- 开工时最大风险：从 F0D 空库引导角色/Schema 的失败原子性、真实 OIDC 浏览器链、ClamAV 冷启动和备份恢复身份边界。

## 2026-08-11 任务1独立运行底座（历史里程碑，已被任务3覆盖）

- 新增单入口 `./scripts/localctl` 与独立 `docker-compose.local.yml`；状态、secret、备份目录均位于被忽略的 repo-local `.local/`。
- 本轮仅创建带随机 project-id、Compose project 和 `io.anhuan.scope=engineering-closeout` 标签的资源；PostgreSQL、Keycloak、MinIO、Redis、ClamAV、API、worker、dispatcher 不暴露宿主端口，只有 web 使用随机 loopback 端口。
- secret 已按 migrator、seed、provisioner、API、worker、dispatcher、PostgreSQL、Keycloak、MinIO 分卷；目录 0700、文件 0600。API/worker/dispatcher 不持有 bootstrap 或 migration DSN。
- 根 F0 revision 与 `migrations/env.py` 保持未改；closeout helper 在同一外部 PostgreSQL 事务内执行 F0D→F1，并在提交前核双 head、31 张 P2–P7 表 FORCE RLS、runtime role flags/membership、definer owner。
- 真实空库迁移：`f0d_0006/f1_0010` 成功；同库第二次迁移成功且无 upgrade 记录；bootstrap-only 合成 seed 成功。
- 完整栈从精确 reset 后启动成功；9 个核心容器均 `running + healthy`，web、API liveness/readiness、OIDC discovery 均通过，输出 `LOCAL_READY`。
- 前端在固定 Node 镜像内真实完成 `tsc -b && vite build`；仍保留约 1.48 MiB chunk warning，作为非阻断技术债。
- 当时状态：`TASK1_RUNTIME_BASE_SMOKE_PASSED / NOT_PRODUCTION`。本节记录的 backup、restore、verify、失败原子性反测和真实浏览器缺口已由后续任务闭合，现役结论以任务3和本文末的最终收口节为准。
- P2–P8 旧定向合同已将四个过期的“仓库当前唯一 head”断言从 `f1_0005` 对齐为 `f1_0010`；这只修正当前 head 预期，不改变 P2 迁移自身的 revision/down_revision 语义，也未改 `f1_0001` 至 `f1_0010`。lockfile `npm ci` 后，P2–P8 137 项与 closeout migration 10 项合计 `147/147 OK`、skipped=0。
- PWA 构建缓存已改为按完整 dist（包含 Service Worker 模板）SHA-256 隔离；注入器对目录和文件使用 `O_NOFOLLOW` + 同一 fd 快照校验，定向 `4/4 OK`。当时仅是构建合同；后续真实浏览器离线/更新已闭合，OS 级安装仍是 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`。

## 2026-08-11 任务2数据可验证与可恢复底座（已通过）

- 真实 `localctl verify` 通过：F0D/F1 head 精确为 `f0d_0006/f1_0010`，31 张 P2–P7 表全部 ENABLE + FORCE RLS，2 个 runtime role 权限与 membership 为预期，2 租户/7 条合成身份绑定完整。
- 真实迁移失败原子性反测通过：唯一随机 scratch DB 在 F1 upgrade 后注入失败，F0/F1 schema、version table、relation、routine 残留均为 0；同 scratch 正常迁移达到双 head，最终精确删除且残留 0。
- 真实 PostgreSQL + MinIO 备份成功，manifest 仅包含聚合摘要，备份目录/文件权限为 `0700/0600`，备份后 9 个核心容器和 HTTP/OIDC 健康全绿。
- 完成一次精确 `reset → restore`。首轮恢复暴露“全新 cluster 缺 F1 definer roles”真实接缝；改为先在新卷运行原子 migrator 预置受保护角色，再单事务 `pg_restore`。第二轮恢复成功，且 health + verify + 迁移反测再次全绿。
- 当时状态：`TASK2_RECOVERY_AND_DATABASE_VERIFY_PASSED / NOT_PRODUCTION`。当时尚未完成的 P2–P7 全链真实 API/RLS、故障恢复与真实 OIDC 浏览器已由任务3闭合；OS 级 PWA 安装明确延期为人工环境门。

## 2026-08-11 任务3业务与浏览器工程闭环（主门已冻结）

- 基础前端 API client 已改为固定 reason code、结构化 status/retryable、AbortSignal；不再读取或显示任意错误响应正文。
- 旧页面中 9 处会形成 `/api/api/v1/...` 的重复前缀已改为统一 `/api/v1/...`；随后真实 `tsc -b && vite build` 通过。
- 同一隔离本地栈内的三条验证已跑通：P2/P4–P7 真实 API+RLS、P3 PostgreSQL+MinIO+ClamAV 故障恢复、真实 Keycloak 多角色浏览器与 PWA 离线/更新。
- P2 与 P4–P7 已在同一专用 PostgreSQL 集群的随机 scratch DB 真实跑通：五段 API 主链各 1、跨租户 API/RLS 读写泄漏 0、timeline/audit/notification 缺口 0、外部调用 0、持久业务库行数变化 0、scratch DB 残留 0；已关闭任务再次关闭返回 409，业务/audit/timeline/notification 四类事务漂移均为 0；应用 SQLAlchemy engine/factory 重建后仍可读 P2/P4/P5/P6/P7 共 5 类关键业务行。输出 `LOCAL_BUSINESS_VERIFY_OK`。
- P3 已在同一栈真实跑通 PostgreSQL + MinIO + ClamAV：上传版本 1、真实 clean scan 1、page-text preview unit 1、release 对象 1、扫描前 release=409；跨租户 API/RLS 可见数 0、对象 SHA 错配 0、对象/bucket/scratch DB 残留 0、持久源库变化 0；`LOCAL_INGESTION_VERIFY_OK`。
- 任务3完成后状态：业务、故障恢复、浏览器和 PWA 技术主门已通过；治理审计后当前统一状态为 `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION`。
- 真实 Keycloak/OIDC 阻断已闭合：运行态旧用户因缺 `firstName/lastName` 触发 `VERIFY_PROFILE`，provisioner 现会幂等修复资料后再写 non-temporary 密码；7/7 合成身份真实认证成功，定向合同 `14/14 OK`。
- P3 文档列表在空筛选条件下的 PostgreSQL `NULL` bind 类型推断阻断已修，四个可选参数显式 cast；P3 定向合同现为 `23/23 OK`。
- PWA installability 与运行时阻断已闭合：Nginx 以 `application/manifest+json` 提供 manifest；Service Worker 真实注册并控制页面；离线状态同时依赖 `navigator.onLine` 与 no-store API 可达性。OS 级应用安装未形成完整证据，已停止继续自动探测，保持 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`。
- 真实 Chromium 主链通过：3 类真实 OIDC 身份均完成认证；管理员访问 17 个顶层页面、顾问 2 个角色页面、企业 2 个角色页面；管理员 API 响应 92 次且非 2xx 为 0；租户 header 切换 1、旧租户状态清空 1；PWA 注册/控制均为 1，敏感 cache entry 为 0，离线静态壳为 1。输出 `LOCAL_BROWSER_VERIFY_OK`。
- 真实 PWA A→B 同源更新链已通过：页面通过受信任的“检查更新”和“应用更新”操作完成 waiting update；`controllerchange=1`，旧应用 cache 已删除、新 cache 已保留，非本应用 sentinel cache、OIDC 登录态和租户状态均保留。输出 `PWA_WAITING_UPDATE_PASSED`。
- P3 故障恢复反测现已真实闭合：MinIO 使用错误的 verifier 私有凭据时写入返回受控 503、无对象和假 `ready`；恢复正确凭据后以同一 Idempotency-Key 重传，仅复用原版本并成功。ClamAV 连接真实拒绝后任务进入 `held/retry_wait/unavailable`，随后通过公开 `retry` 路由连接常驻 ClamAV 恢复并释放。统一 `localctl verify` 输出新增四项恢复计数均为 1，跨租户、源库变更、对象/bucket/scratch 残留均为 0。
- P3 实现与 verifier 定向合同已在专属 runtime 镜像、只读仓库、无网络条件下执行 `38/38 OK`；统一真实验证结束后 9 个核心容器及 web/API/OIDC 健康仍全绿。
- 统一 `localctl verify` 五门全绿：数据库/迁移、迁移失败原子性、业务/API/RLS、P3 摄取故障恢复、日志/secret/PII 边界；成功标签分别为 `LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK`、`LOCAL_LOG_VERIFY_OK`。
- 真实 `stop → start` 已强制重建 9 个核心容器并保留卷；重启后健康全绿，业务数据仍存在，统一 verify 五门再次通过。

## 2026-08-11 PDF Inspector 架构决策（已完成，运行时未启用）

- 唯一交付文件已对齐为 `PDF_INSPECTOR_INTEGRATION.md`，决定将 PDF Inspector 保留为降低材料录入成本的候选影子解析器；本轮状态严格为 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`。
- 只读复核旧 `codex/f1-1-2-pdf-probe`：其 21 PDF/248 页双重放标签为 `SHADOW_PROBE_COMPLETE_NOT_EVALUATED`，可支撑隔离架构考虑，但没有准确率、Gold、客户材料或生产证据；未复制旧源码、依赖、产物或历史。
- 已知 `pdf-inspector 0.2.6` 实际依赖 `lopdf 0.41.0` 且受 `RUSTSEC-2026-0187` 影响；旧发布包继续禁止进入 API、worker、镜像和任意上传主链。
- 未来门固定为：patched pinned build；P3 源身份匹配且 ClamAV `clean` 后；默认 OFF、进程外、无网、无 secret、资源受限 shadow；`pypdf` 权威/fallback；结果仅为人工确认前草稿。
- 本项没有修改 requirements、lock、Compose、migration、代码或测试，没有安装或运行 PDF Inspector，也没有处理真实材料。

## 2026-08-11 工程完成门（技术已通过，治理待收口）

- 已冻结的代码与运行门包括：统一 verify 五门、9 核心容器强制重建后数据持久、业务非法 409 与事务零漂移、应用 engine 重启读 5 类业务、P3 MinIO/ClamAV 故障恢复，以及多角色浏览器/PWA waiting update 链。
- 最终直接相关检查 `230/230 OK`。
- 备份 `20260810T224332Z-2a861bccbba9` 已完成真实 `reset → restore`；恢复后 health ready，统一 verify 五门再次全绿。
- 恢复后真实浏览器再验通过：3 类身份，管理员 17 页、顾问 2 页、企业 2 页，管理员 API 92 次且非 2xx 为 0，`PWA_WAITING_UPDATE_PASSED`。
- PDF Inspector 保持 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`；OS 级 PWA 安装保持 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`。
- 上述是已保留的技术摘要，不是新的治理重放证据。当前结论：`TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION`。版本只进入 `codex/engineering-closeout` 工程分支，未部署、未进入真实 UAT 或生产。

## 2026-08-11 浏览器停止与残留收口

- 按用户最新要求停止所有 Chrome/PWA OS 预检、GUI/AX 探针和 browser-verify 重跑；后续不再用完整浏览器链调试 OS 安装。
- 保留真实 `pwa-update` 通过证据；macOS 安装、在线启动、真实停站离线重开、卸载继续为 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`。
- 本轮临时 Chrome/CFT 进程、`anhuan-cft-*`/`anhuan-chrome-*`/`anhuan-engineering-browser-*` profile、PWA control/recovery、MinIO canary 目录和“安环内部工作台”shim 核对均为 0；临时 PWA B 镜像为 0。
- 专属 Compose 栈已停止，9 个核心容器均为 exited；数据库与对象卷保留，未 reset、未删除共享数据。

## 2026-08-11 治理审计修正

- 精确 PDF 交付物统一为 `PDF_INSPECTOR_INTEGRATION.md`；带额外决策后缀的旧名称已废止，不再存在独立或平行权威文件。
- 允许文件地界、冻结文件和共享文件单写者规则已写入 `ENGINEERING_CLOSEOUT_TASKBOOK.md`。地界外改动必须先授权，不得事后追认。
- 历史实现没有可靠的逐轮编号和完整原始命令证据，因此不倒推、不补造；以下轮次表从治理修正 G1 开始，最多 12 轮。

### 聚合轮次表（最多 12 轮）

| 轮次 | 目标 | 唯一写者 / 允许文件 | 预期证据 | 结果 | 状态 |
| --- | --- | --- | --- | --- | --- |
| G1 | 修正精确 PDF 文件名、降级状态、固化地界与证据模板 | `docs_truth_update`；仅本轮获授权的 Markdown | `git diff --check` 退出 0；旧文件名零命中；7 份现役状态声明一致 | `git diff --check -- '*.md'` 退出 0；旧文件名零命中；现役状态均为治理待收口 | `DOCS_DIFF_CHECK_PASSED` |
| G2 | 收敛治理审计后已出现但尚未登记的实现变更；冻结备份恢复、健康、租户切换和冻结文件边界 | 主 agent：`local_backup.py`、`local_seed.py`、`localctl`、backup/verify/keycloak tests 与 Docker ignore；`log_gate_audit`：`health.py`+health test；`browser_role_coverage`：`api.ts`、`Layout.tsx`+frontend API test | 从本行登记后的最终补丁与检查才计证据；冻结文件相对根提交零 diff；相关定向测试全绿、skipped=0；`git diff --check`=0 | 2026-08-10T23:18:06Z–23:34:18Z；宿主等价环境 244/244 OK、skipped=0；冻结文件相对根提交零 diff；diff-check=0。首次容器入口暴露缺 Node，已改为20模块固定Python镜像+真实Web构建；容器复跑因审批通道断线留给T0，不冒充已过 | `HOST_TARGETED_PASSED / LIVE_REPLAY_PENDING` |
| G3 | 实现并定向验证真实 secret 权限、MinIO/ClamAV 故障恢复与外来 sentinel reset 边界 | 主 agent；仅 `scripts/localctl`、`tests/test_engineering_closeout_reverse.py`、本轮治理/运维 Markdown | 固定聚合输出；所有故障均 finally 恢复；Docker只按精确ID和双标签操作；单元全绿、skipped=0；live证据未跑前保持 pending | 2026-08-10T23:34:18Z–23:57:51Z；active secret改为synthetic镜像探针；stop/create前0600 journal、下次命令精确恢复；dependency ID drift、volume-only、双清理失败、wrong-mode零Docker等9/9通过；独立只读复核无journal P0/P1；live未跑 | `STATIC_TARGETED_PASSED / LIVE_REPLAY_PENDING` |
| G4 | 收口真实浏览器角色权限、404/409/503、ClamD不可用恢复与PWA更新证据 | 主 agent集成`localctl`；`browser_role_coverage`负责runner、3个前端可测性接缝和定向test；本轮治理/运维Markdown | 3身份真实OIDC；allowed_actions DOM；跨租户404、非法状态409；真实MinIO 503同key恢复；ClamD unavailable→ready；PWA waiting；精确validator与单测；live前pending | 受限 Compose supervisor、故障链和分段浏览器 runner 已接入。真实 `pwa-update` 分段输出 `LOCAL_PWA_UPDATE_VERIFY_OK`：waiting=1、受信任应用更新点击=1、controllerchange=1、旧cache删除2、新cache保留2、sentinel与登录态保留1、敏感cache=0。3个前端接缝和2个定向运行镜像文件未在最初地界表事前列出，已在 Taskbook 如实登记范围偏差，不追认。用户随后明确停止继续启动浏览器调试 OS 安装；该项不补造证据。 | `PWA_UPDATE_LIVE_PASSED / PWA_OS_INSTALL_NOT_TESTED / GOVERNANCE_REPLAY_PENDING` |

G5–G12 尚未启动。任何后续轮必须先在此表登记；历史临时尝试不得填入空行冒充可审计证据。

### 最终证据表（全部待真实重放）

T0 冻结命令如下；只能按实际结果填写表格，不得把历史计数抄作本次输出：

```bash
./scripts/localctl test
```

| # | 实际命令 / 动作 | 开始/结束时间 | 执行 commit | 退出码 | 固定输出/聚合计数 | 残留计数 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T0 | `./scripts/localctl test`（仓内冻结 21 个 Python 模块 + 真实 Web/P8 构建） | — | — | — | `LOCAL_TARGETED_TESTS_OK`；要求 `>=137`、web_builds=1、failures=0、errors=0、skipped=0 | — | `PENDING_REPLAY_EVIDENCE` |
| 1 | `./scripts/localctl reset --confirm-local-data` | — | — | — | `LOCAL_RESET_OK` | 0 | `PENDING_REPLAY_EVIDENCE` |
| 2 | `./scripts/localctl start` | — | — | — | `LOCAL_READY <loopback-url>` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 3 | `./scripts/localctl migrate` | — | — | — | `LOCAL_MIGRATE_OK` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 4 | `./scripts/localctl migrate` | — | — | — | `LOCAL_MIGRATE_OK`，无额外 upgrade | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 5 | `./scripts/localctl seed` | — | — | — | `LOCAL_SEED_OK` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 6 | `./scripts/localctl health --json` | — | — | — | 9 核心服务与 HTTP/OIDC 全 true | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 7 | `./scripts/localctl verify` | — | — | — | verify 五门固定标签 | 0 | `PENDING_REPLAY_EVIDENCE` |
| R1 | `./scripts/localctl dependency-verify` | — | — | — | `LOCAL_DEPENDENCY_BOUNDARIES_OK`；synthetic 0644拒绝、MinIO/ClamD health+readyz红后恢复 | 0 | `PENDING_REPLAY_EVIDENCE` |
| 8 | `./scripts/localctl stop` | — | — | — | `LOCAL_STOPPED` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 9 | `./scripts/localctl start` | — | — | — | `LOCAL_READY <loopback-url>` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 10 | `./scripts/localctl health --json` | — | — | — | 重启后全 true | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 11 | `./scripts/localctl verify` | — | — | — | 重启后 verify 五门固定标签 | 0 | `PENDING_REPLAY_EVIDENCE` |
| 12 | `./scripts/localctl backup` | — | — | — | `LOCAL_BACKUP_OK <backup-id>` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 13 | `./scripts/localctl reset --confirm-local-data --prove-foreign-sentinel` | — | — | — | `LOCAL_FOREIGN_SENTINEL_OK`；外来container+volume存活后精确清理 | 0 | `PENDING_REPLAY_EVIDENCE` |
| 14 | `./scripts/localctl restore --backup-id <id> --confirm-local-data` | — | — | — | `LOCAL_RESTORE_OK <backup-id>` | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 15 | `./scripts/localctl health --json` | — | — | — | 恢复后全 true | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 16 | `./scripts/localctl verify` | — | — | — | 恢复后 verify 五门固定标签 | 0 | `PENDING_REPLAY_EVIDENCE` |
| 17 | `./scripts/localctl browser-verify` | — | — | — | `LOCAL_BROWSER_VERIFY_OK`；OS install 仍 `NOT_TESTED` | 0 | `PENDING_REPLAY_EVIDENCE` |
| 18 | `./scripts/localctl health --json` | — | — | — | 浏览器验证后全 true | 待记录 | `PENDING_REPLAY_EVIDENCE` |
| 19 | `./scripts/localctl stop` | — | — | — | `LOCAL_STOPPED` | 0 | `PENDING_REPLAY_EVIDENCE` |

此表是待重放模板，不是已执行声明。只有按顺序填入真实时间、commit、退出码和固定输出后，才能恢复 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
