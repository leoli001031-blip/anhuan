# Engineering Closeout Progress

## 2026-08-11 任务0开工回执

- 目标：单人可启动、维护、恢复并用真实浏览器操作 P2–P8，最终仅 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
- 顺序：安全分支 → localctl/独立栈 → 数据库/RLS/后端 → OIDC/前端/PWA → 备份恢复 → 最终工程门。
- 基线：fresh clone `origin/main@8d2e791`，单根提交、564 文件，tree=`2070ced3fce8b0763dd6c8a2419414b92a702be2`。
- 分支：`codex/engineering-closeout`；未引入旧 repair/PDF Probe 历史，未 push、未部署。
- 当前 F1 唯一源码 head 为 `f1_0010`。
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
- P2–P8 旧定向合同已将四个过期的 `f1_0005` head 断言对齐为唯一 `f1_0010`；lockfile `npm ci` 后，P2–P8 137 项与 closeout migration 10 项合计 `147/147 OK`、skipped=0。
- PWA 构建缓存已改为按完整 dist（包含 Service Worker 模板）SHA-256 隔离；注入器对目录和文件使用 `O_NOFOLLOW` + 同一 fd 快照校验，定向 `4/4 OK`。当时仅是构建合同；后续真实浏览器离线/更新已闭合，OS 级安装仍是 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`。

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
- 任务3完成后状态：业务、故障恢复、浏览器和 PWA 主门已通过；项目最终状态已由本文末的工程完成门收口为 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
- 真实 Keycloak/OIDC 阻断已闭合：运行态旧用户因缺 `firstName/lastName` 触发 `VERIFY_PROFILE`，provisioner 现会幂等修复资料后再写 non-temporary 密码；7/7 合成身份真实认证成功，定向合同 `14/14 OK`。
- P3 文档列表在空筛选条件下的 PostgreSQL `NULL` bind 类型推断阻断已修，四个可选参数显式 cast；P3 定向合同现为 `23/23 OK`。
- PWA installability 与运行时阻断已闭合：Nginx 以 `application/manifest+json` 提供 manifest；Service Worker 真实注册并控制页面；离线状态同时依赖 `navigator.onLine` 与 no-store API 可达性。OS 级应用安装未执行，保持 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`。
- 真实 Chromium 主链通过：3 类真实 OIDC 身份均完成认证；管理员访问 17 个顶层页面、顾问 2 个角色页面、企业 2 个角色页面；管理员 API 响应 92 次且非 2xx 为 0；租户 header 切换 1、旧租户状态清空 1；PWA 注册/控制均为 1，敏感 cache entry 为 0，离线静态壳为 1。输出 `LOCAL_BROWSER_VERIFY_OK`。
- 真实 PWA A→B 同源更新链已通过：页面通过受信任的“检查更新”和“应用更新”操作完成 waiting update；`controllerchange=1`，旧应用 cache 已删除、新 cache 已保留，非本应用 sentinel cache、OIDC 登录态和租户状态均保留。输出 `PWA_WAITING_UPDATE_PASSED`。
- P3 故障恢复反测现已真实闭合：MinIO 使用错误的 verifier 私有凭据时写入返回受控 503、无对象和假 `ready`；恢复正确凭据后以同一 Idempotency-Key 重传，仅复用原版本并成功。ClamAV 连接真实拒绝后任务进入 `held/retry_wait/unavailable`，随后通过公开 `retry` 路由连接常驻 ClamAV 恢复并释放。统一 `localctl verify` 输出新增四项恢复计数均为 1，跨租户、源库变更、对象/bucket/scratch 残留均为 0。
- P3 实现与 verifier 定向合同已在专属 runtime 镜像、只读仓库、无网络条件下执行 `38/38 OK`；统一真实验证结束后 9 个核心容器及 web/API/OIDC 健康仍全绿。
- 统一 `localctl verify` 五门全绿：数据库/迁移、迁移失败原子性、业务/API/RLS、P3 摄取故障恢复、日志/secret/PII 边界；成功标签分别为 `LOCAL_VERIFY_OK`、`LOCAL_MIGRATION_ATOMICITY_OK`、`LOCAL_BUSINESS_VERIFY_OK`、`LOCAL_INGESTION_VERIFY_OK`、`LOCAL_LOG_VERIFY_OK`。
- 真实 `stop → start` 已强制重建 9 个核心容器并保留卷；重启后健康全绿，业务数据仍存在，统一 verify 五门再次通过。

## 2026-08-11 PDF Inspector 架构决策（已完成，运行时未启用）

- 新增 `PDF_INSPECTOR_INTEGRATION_DECISION.md`，决定将 PDF Inspector 保留为降低材料录入成本的候选影子解析器；本轮状态严格为 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`。
- 只读复核旧 `codex/f1-1-2-pdf-probe`：其 21 PDF/248 页双重放标签为 `SHADOW_PROBE_COMPLETE_NOT_EVALUATED`，可支撑隔离架构考虑，但没有准确率、Gold、客户材料或生产证据；未复制旧源码、依赖、产物或历史。
- 已知 `pdf-inspector 0.2.6` 实际依赖 `lopdf 0.41.0` 且受 `RUSTSEC-2026-0187` 影响；旧发布包继续禁止进入 API、worker、镜像和任意上传主链。
- 未来门固定为：patched pinned build；P3 源身份匹配且 ClamAV `clean` 后；默认 OFF、进程外、无网、无 secret、资源受限 shadow；`pypdf` 权威/fallback；结果仅为人工确认前草稿。
- 本项没有修改 requirements、lock、Compose、migration、代码或测试，没有安装或运行 PDF Inspector，也没有处理真实材料。

## 2026-08-11 工程完成门最终收口（已通过）

- 已冻结的代码与运行门包括：统一 verify 五门、9 核心容器强制重建后数据持久、业务非法 409 与事务零漂移、应用 engine 重启读 5 类业务、P3 MinIO/ClamAV 故障恢复，以及多角色浏览器/PWA waiting update 链。
- 最终直接相关检查 `230/230 OK`。
- 备份 `20260810T224332Z-2a861bccbba9` 已完成真实 `reset → restore`；恢复后 health ready，统一 verify 五门再次全绿。
- 恢复后真实浏览器再验通过：3 类身份，管理员 17 页、顾问 2 页、企业 2 页，管理员 API 92 次且非 2xx 为 0，`PWA_WAITING_UPDATE_PASSED`。
- PDF Inspector 保持 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`；OS 级 PWA 安装保持 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`。
- 最终结论：`INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。未 push、未部署、未进入真实 UAT 或生产。
