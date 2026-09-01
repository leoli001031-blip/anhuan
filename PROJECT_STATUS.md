# 项目现役状态

更新日期：2026-09-02（A-Eco 本地候选：本日新增云视觉 OCR 适配器（`glm_vision`/`ark_vision` 双 provider，chat/anthropic 双方言，厂商中立 backend `cloud-vision-chat-1`，fail-closed，22 项离线合同 OK）与 `f1_0024` 迁移；经用户授权完成一次真实 live 冒烟：`glm-5.3-flash`（anthropic 方言/Coding Plan 额度）3.5s 转录扫描页 116 字、4/4 关键词命中 `CLOUD_OCR_LIVE_SMOKE_PASSED / OCR_ACCURACY_NOT_EVALUATED`；当前工作树已实现数据库持久化的上传摄取、分析后投递和报告 generation delivery/outbox、逐页 OCR checkpoint/失败新修订、token fence、历史 actor rebind、DB-only status 及敏感写入的列级 DML/trigger 边界；23 项直接合同、隔离 PostgreSQL `f1_0014→f1_0023` 迁移/47 表 FORCE-RLS 目录、真实 Redis/worker 崩溃恢复、真实 PostgreSQL OCR checkpoint 恢复及当前工作树浏览器 UAT 已通过，当前为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`；人工视觉为 `NOT_PART_OF_THIS_RUN`；`f1_0023` 无 OCR 候选源码包已于 2026-09-01 经用户授权上传测试服务器并以新 project 启动，11/11 容器 healthy，native-text PDF 远端定向冒烟通过，状态为 `REMOTE_NATIVE_TEXT_SMOKE_PASSED / TEST_SERVER_DEPLOYED / NOT_COMMITTED / NOT_PUSHED / NOT_PRODUCTION`；旧 `f1_0020` demo 已 stop 保留（10 容器/14 卷/2 网络）作为回滚点，回滚演练未执行）
本页是当前状态的唯一项目级入口；阶段文档中的早期 `当前`、`READY` 或 `NOT_TESTED` 记录均按其日期保留，不覆盖本页。

## 代码与版本

- 当前候选以仓库根目录为唯一路径基准，不绑定任何宿主机绝对 checkout 路径；本地分支/HEAD 为 `codex/material-report-aeco-polish@955a274990cd37797dbb6ef2c11459b288074ff8`。
- PR 栈最后一次远端取证（2026-08-24）为 #3=`main@dd466e5 → codex/material-report-integration@6cdbba3`、#4=`codex/material-report-integration@6cdbba3 → codex/material-report-aeco-polish@1ea4fe3`，两者 `OPEN / draft / MERGEABLE / statusCheckRollup=[]`。本轮未连接远端刷新 PR 元数据；`statusCheckRollup=[]` 只表示无 CI 门禁，不是 CI 通过。
- `955a274` 是现役本地已提交基线，当前迁移/readiness/fixture 修正仍是未提交工作树变更。合并顺序仍固定为 #3 先入 `main`，再把 #4 base 改为 `main` 并核差异只剩本层。本轮未 commit、未 push、未改 PR、未 mark ready、未 merge；2026-09-01 曾按用户明确授权向测试服务器部署无 OCR 测试候选（见远端边界），不构成发布。
- PR #2 已于 2026-08-21 合并，合并时 head 为 `codex/material-rag-postgres-integration@af0d74470a81275a64be08638d7272197bd53095`；原“OPEN+draft”说法已经退役。其 worktree 仅保留历史/并行工程现场，不是当前候选 checkout。
- F1 Alembic：源码单一 head 为 `f1_0024`，线性顺序含 `f1_0016 → … → f1_0023 → f1_0024`（0024 将 `material_ocr_checkpoint.parser_backend` 闭集扩为 FIFO `f0h-ppocrv6-3.9.2` + 云端 `ark-vision-chat-1`）；`migrate_f1` 目标闭集同步包含 0014–0024。默认工程仍锁 `f1_0014`，material-RAG 专属目标仍为 `f1_0016`，analysis-report 专属目标为 `f1_0024`；默认 seed/verify/backup 的 0014/35 合同保持不变。
- A-Eco 本地真源闭合为：`infra/f1/analysis-reports/migrate.py` 唯一请求并核验 `f1_0023`；`scripts/localctl analysis-report-demo-*` 拥有专属环境生命周期；`/api/readyz` 的 HTTP 200、`status=ready`、精确组件闭集与 `no-store` 才是运行就绪信号。`deploy/analysis-report/local_candidate.py` 从仓库内相对定位并组合这三者，不以容器存在或某台机器的 checkout 绝对路径代替 readiness。
- 分析报告历史取证：只在上一版专属本地候选以 `tenant-a` / `invitee` 合成身份、`evidence_local`、`ark_calls=0`、`mock_data=0` 自动覆盖异步报告、审核证据、HTML 产物、客户服务摘要与带引用材料问答。它不是当前 `f1_0023` 工作树、正式评分器、生产 worker、真实 Ark/RAGFlow 或客户数据能力。
- 当前 `f1_0023` 工作树新增上传/分析/报告三段数据库 delivery、报告 job/version 与 generation outbox 同事务注册、DB dispatcher + dispatch-token fence、历史 actor rebind 和 DB-only status；同时撤销报告/version/job/content/audit/review-evidence/health-snapshot 的表级敏感 DML，改为列级授权与状态/发布/可见性/证据 trigger。这些差异已取得合同、真实迁移、Redis/worker 崩溃恢复、OCR checkpoint 恢复的 `TARGETED_TEST_PASSED`，以及使用 fixture 材料的当前浏览器流程 `LOCAL_WORKFLOW_UAT_PASSED`。
- 当前残余风险包括共享的 `f1_api` API/report-worker 角色、数据库无法独立重算健康评分内容、ingestion worker 的 `f1_api` + MinIO root、DB finalize 失败后的 MinIO orphan、分析/索引重复 OCR、仅 ARM64 的 OCR 镜像、只自动回填 latest eligible version、非 latest 历史版本仍需手工处理，以及 Redis 长期故障时报告 delivery 持续重试而 job status 尚未投影 delivery attempt/reason 的运维可观测性缺口。
- 远端边界：Chrome/Tencent TAT 只读取证确认服务器为 `x86_64/amd64`、2 vCPU/4 GiB、Ubuntu 24.04、Docker 29、Compose 2.40；当时内存约 2.6 GiB 已用/1.0 GiB 可用、空闲 swap 约 1.4 GiB、磁盘可用约 47 GiB，且没有绑定 SSH key。服务器并非空白机：Compose project `anhuan-ar-demo-0c74bf2a11ee` 的 10 个容器 healthy 约 20 小时，web 仅监听 `127.0.0.1:58103`，health/ready 正常，数据库 head=`f1_0020`；但远端缺少 0021/0022/0023 迁移、关键文件哈希与当前工作树不同，且没有 OCR container。因此精确状态为：当前 `f1_0023` 工作树 `NOT_DEPLOYED`；服务器历史 `f1_0020` demo `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。随后已补齐 amd64 无 OCR 候选模式并通过 Compose 合并检查；2026-09-01 用户明确授权后，仓外 2 MiB 无秘密源码包上传测试服务器，旧 `f1_0020` demo 只 `stop`、不 `down`、不删卷（10 容器全部停止保留、14 卷、2 网络），新 `f1_0023` 栈以新版本目录/新 project 启动：11/11 容器 healthy、全部 `OOMKilled=false`，web 仅监听服务器 loopback，经 SSH 隧道以 `127.0.0.1:44087` 访问。远端 native-text PDF 定向冒烟通过：上传 `202` → ClamAV `clean` → `ocr_required=false` → 预览/Redis 调度/本地索引 `ready` → 自动生成第 1 版报告草稿（7 节，引用含本次 PDF），未提交/审批/发布，客户端已发布列表前后不变；服务器回执 `/home/ubuntu/aeco-test/smoke-evidence/native-pdf-smoke-c81e611634c64655bdad33d35c10223c.json`，并保留 `ZZ-SYNTH-AUTO-*` 合成资料与草稿作证据。未覆盖：远端浏览器 E2E（`NOT_TESTED_POLICY_BLOCKED`，未绕过 localhost 安全策略）、远端 Redis/worker 故障注入与回滚演练、扫描件 OCR（`OCR_REQUIRED` fail-closed）、DNS/TLS/公网入口与监控；服务器密码曾在对话中明文出现且未绑定 SSH key，待轮换。
- 旧 `codex/f1-1-1-repair` 只保留作历史证据，不再继续开发或推送

## 阶段状态

| 阶段 | 当前标签 | 已验证层 | 未开放边界 |
| --- | --- | --- | --- |
| A-Eco 分析报告候选 | `BLOCKED / TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / REMOTE_NATIVE_TEXT_SMOKE_PASSED / TEST_SERVER_DEPLOYED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / REMOTE_BROWSER_E2E=NOT_TESTED_POLICY_BLOCKED / NOT_COMMITTED / NOT_PUSHED / NOT_PRODUCTION` | 当前差异的 23 项直接合同；真实隔离 PostgreSQL `f1_0014→f1_0023`、47 表 FORCE-RLS 与关键 definer owner；真实 Redis unavailable/恢复、worker SIGKILL、Redis 队列丢失、stale token 拒绝及 PostgreSQL 恢复；真实 PostgreSQL 加密 OCR checkpoint 恢复/过期清理；当前浏览器 workflow UAT；无 OCR 候选远端 native-text PDF 定向冒烟（上传→ClamAV→解析→预览/索引→报告草稿，11/11 healthy） | macOS runtime 使用独立 OS 子进程中的 RQ `SimpleWorker`，不是 Linux 容器 `Worker.main` 启动证明；OCR 使用 synthetic adapter，不是准确率证明；浏览器 UAT 使用预置 eligible fixture，不是浏览器上传→OCR→索引→报告全链；当前工作树未 commit/push，远端部署物来自未提交工作树的仓外包（有 SHA 回执、无可审计 Git 提交）；远端为 loopback+SSH 隧道，无公网入口；远端故障注入、回滚演练与浏览器 E2E 未执行；服务器无 amd64 OCR，扫描件 `OCR_REQUIRED`；历史过程违规仍使整体保持 `BLOCKED` |
| 工程收口 | `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION` | 已保留 230/230、verify 五门、reset/restore、重启、浏览器/PWA 技术摘要 | 精确顺序的治理证据重放表仍为 pending；OS 级 PWA 安装因浏览器自动化边界未测；未 UAT、未生产 |
| F1.1.1 | `F1_1_1_PAUSED_NOT_ACCEPTED` | 历史修复与拒绝证据保留 | formal/reverse/SBOM/clean/M4 未恢复；tracked v0.3 仍为 `F1_1_1_REJECTED` |
| P2 | `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION` | 真实 API + FORCE RLS 主链、跨租户边界、非法关闭 409 与事务零漂移；真实 Keycloak 合成身份 | 真实客户资料、发布验收、生产 |
| P3 | `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL + MinIO + ClamAV 上传、扫描、预览、释放；MinIO 写失败与 ClamAV 不可用后恢复；跨租户 404 | 真实客户资料、生产容量、正式解析引擎 |
| P4 | `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的 CRM、报告快照主链 | 正式 PDF/HTML、签发、发布、真实客户数据 |
| P5 | `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的提交、独立审核、发布状态与影响任务 | 联网法规源、法律意见、外部发布 |
| P6 | `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 合成 Oracle 与分歧流程，外部调用为 0 | Gold、真实 OCR/LLM 准确率、发布门 |
| P7 | `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL/API/RLS 人工结果与回滚门；本地 PostgreSQL + MinIO 备份/恢复链 | 故障切换、部署或生产访问 |
| P8 | `P8_COMPLETE_NOT_RELEASE_VERIFIED / INTERNAL_PWA_ONLY / NOT_PRODUCTION` | 3 类 OIDC 身份；管理员 17、顾问 2、企业 2 页；离线静态壳与真实 A→B waiting update 用户确认链 | OS 级应用安装 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`、设备矩阵、正式小程序发布 |
| 材料录入降本 | `SMOKE_PASSED / NOT_PRODUCTION` | 实库 `f1_0011 → … → f1_0014`；同一合成文本 PDF 在服务公司／客户域各上传一次，真实 MinIO/ClamAV、预览、释放、2 analysis/2 page/8 candidate、2 scope、负责人/非负责人/跨租户上下层 RLS 及客户材料 API+DB 政策硬拒绝通过；服务公司 policy draft=1、publication=0 | 真实 Demo PDF、批量浏览器、物理 RAG 索引/检索、OCR、准确率、备份恢复实跑、P4 报告入口、Inspector 运行时、发布验收与生产 |
| 双知识域物理 RAG | `PRODUCTION_SHAPED_WORKER_RUNTIME_PASSED / MATERIAL_RAG_RESTORE_PREFLIGHT_PASSED / LIVE_RETRIEVAL_AUTH_PENDING / BACKEND_DURABLE_ORCHESTRATION_LOCAL_PASSED / MIGRATION_CLOSEOUT_CONTRACT_PASSED / MATERIAL_RAG_BACKUP_RESTORE_RUNTIME_PASSED / CRASH_RECOVERY_DB_RESTORED_SIGKILL_PASSED / CRASH_RECOVERY_POWER_LOSS_NOT_TESTED / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION` | worker 合同+真实 PG live；preflight 合同且 `destructive_started=0`；合并/clone `Ran 89 / OK` | 正式用户 restore apply、领导 UAT、Ark live、生产、断电。未写 `UAT_PASSED` / `PRODUCTION_WORKER_PASSED` / `CRASH_RECOVERY_RUNTIME_PASSED`。 |

已保留的技术摘要为：直接相关检查 `230/230 OK`，备份 `20260810T224332Z-2a861bccbba9` 完成 `reset → restore`，恢复后 health ready、verify 五门全绿、浏览器与 PWA 更新链通过。这些摘要不替代当前 pending 的精确顺序重放证据表。P8 构建仍有单 JS 约 1.48 MiB 的非阻断性能债。

## 运行与发布边界

- 2026-08-31 Chrome/Tencent TAT 只读观察到远端 Compose project `anhuan-ar-demo-0c74bf2a11ee` 的 10 个容器 healthy 约 20 小时，web=`127.0.0.1:58103`，health/ready 正常，数据库 head=`f1_0020`。2026-09-01 经授权部署后：旧栈只 `stop`、未 `down`、未删卷（10 容器全部 stopped 保留、14 卷、2 网络），作为回滚点，回滚演练（新栈停→旧栈起→readiness/数据核对）尚未执行；新 `f1_0023` 无 OCR 栈以新版本目录/新 project 运行，11/11 容器 healthy、`OOMKilled=false`，web 仅监听 loopback，经 SSH 隧道 `127.0.0.1:44087` 访问；native-text PDF 远端冒烟通过，服务器留有 smoke 回执 JSON 与 `ZZ-SYNTH-AUTO-*` 合成证据。远端浏览器 E2E（localhost 安全策略阻止，未绕过）、Redis/worker 故障注入、主机重启、扫描件 OCR、公网 DNS/TLS 与监控告警均未执行/未部署；服务器密码曾在对话中明文出现且未绑定 SSH key，待轮换。
- 材料录入分支已在专属、双标签本地 Compose 栈从实库 `f1_0011` 迁移到 `f1_0014`，并在随机 scratch 数据库和随机 MinIO 桶将同一份合成 PDF 分别按服务公司域、客户域验证。第一次迁移因 `FORCE RLS` 遮蔽旧文档回填而失败且事务整体回滚；限定 bootstrap session 的有界 `RESET ROLE` 回填修复后重跑得到 `LOCAL_MIGRATE_OK`。提交前又以 `f1_0014` 收紧底层原件/受控任务读取并完成同一验证。没有使用旧共享 `anhuan-f1` 栈作证。
- 默认工程 closeout 栈目前停止且保留数据卷：本机可见 9 个 `anhuan-closeout-*` 容器全部 Exited，对应卷仍在。material-RAG 最近一次重放的专属 container、volume、network、runtime image 残留为 0。两者不是同一运行环境。
- 宿主另有桌面 checkout 的共享 `anhuan-f1`（本窗口实测 15 容器全部 exited）和历史 `anhuan-f0d`。本窗口未启停任何共享容器。共享栈仍不是索引、检索或工程完成证据。
- 真实 `stop → start` 会强制重建 9 个核心容器但保留卷；重启后数据库、业务行和统一 verify 五门仍通过。
- scratch 数据库和 P3 临时对象只用于 verifier，每轮结束后精确删除。旧共享 `anhuan-f1` 栈不是本轮工程完成证据。
- 未执行真实客户/人工 UAT、生产部署、生产数据迁移、正式小程序发布或客户数据验证。
- 当前 runtime 恢复门在隔离 PostgreSQL、专属 Redis 7、真实 RQ 队列及生产 `run_generation_job` 入口上通过：Redis 不可用时数据库 delivery 进入 `retry_wait`，恢复后 worker 取得 lease；SIGKILL worker 进程组并删除/重建空 Redis 后，过期 lease 由数据库重投，旧 token 无法提交，新 token 从 PostgreSQL 恢复到 `draft/done`（7 节、2 引用）。macOS 的普通 RQ Worker fork 受 Objective-C runtime guard 影响，本轮用独立 OS 子进程中的 RQ `SimpleWorker`；这证明任务/数据库/Redis/token 恢复语义，不证明 Linux 容器 `Worker.main` 启动链。
- OCR checkpoint 恢复门在真实隔离 PostgreSQL 上通过：页 1/3 以 AES-GCM 加密落库且无明文，新进程解密后仅重试缺失页 2，最终 analysis=`ready`、OCR debt=0、成功 checkpoint 清零；另以真实过期条件验证有界 purge 删除 1 条。OCR adapter 为 synthetic，结论不包含 OCR 准确率或正式引擎验收。
- 当前工作树浏览器 workflow UAT 运行 111.933 秒并 exit=0，输出 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`；报告 7 节/2 引用、审核、发布/撤回、客户列表/详情/服务摘要/材料问答与无伪健康评分均通过，专属资源清理为 0、共享 demo 指纹未变。该门使用预置 eligible fixture 材料，不覆盖浏览器上传→OCR checkpoint→索引→报告的完整链路。
- F1.1.1 formal/reverse/SBOM/clean/M4 不恢复；本轮最高只能到 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。

## 文档入口

- F1.1.1 暂停事实：[F1_1_1_REPAIR_PROGRESS.md](./F1_1_1_REPAIR_PROGRESS.md)
- P2：[P2_BUSINESS_WORKBENCH_PROGRESS.md](./P2_BUSINESS_WORKBENCH_PROGRESS.md)
- P3：[P3_CONTROLLED_INGESTION_PROGRESS.md](./P3_CONTROLLED_INGESTION_PROGRESS.md)
- P4：[P4_VIEWS_REPORTS_CRM_PROGRESS.md](./P4_VIEWS_REPORTS_CRM_PROGRESS.md)
- P5：[P5_POLICY_WORKFLOW_PROGRESS.md](./P5_POLICY_WORKFLOW_PROGRESS.md)
- P6：[P6_AUTOMATED_QUALITY_PROGRESS.md](./P6_AUTOMATED_QUALITY_PROGRESS.md)
- P7：[P7_LOCAL_PRODUCTION_REHEARSAL_PROGRESS.md](./P7_LOCAL_PRODUCTION_REHEARSAL_PROGRESS.md)
- P8：[P8_INTERNAL_PWA_PROGRESS.md](./P8_INTERNAL_PWA_PROGRESS.md)
- 工程收口：[ENGINEERING_CLOSEOUT_PROGRESS.md](./ENGINEERING_CLOSEOUT_PROGRESS.md)
- 本地运行：[LOCAL_OPERATIONS.md](./LOCAL_OPERATIONS.md)
- 排障：[TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- 备份恢复：[RECOVERY.md](./RECOVERY.md)
- PDF Inspector 决策：[PDF_INSPECTOR_INTEGRATION.md](./PDF_INSPECTOR_INTEGRATION.md)
- 材料录入切片：[MATERIAL_INTAKE_PROGRESS.md](./MATERIAL_INTAKE_PROGRESS.md)
- 双知识域物理 RAG：[任务书](./MATERIAL_RAG_TASKBOOK.md)／[进展证据](./MATERIAL_RAG_PROGRESS.md)／[当前阻塞](./MATERIAL_RAG_BLOCKED.md)／[backup/restore 设计](./MATERIAL_RAG_BACKUP_RESTORE_DESIGN.md)／[离线 UAT 机器门](./MATERIAL_RAG_UAT_REPORT.md)
- 本地 Fixture 使用边界：[LOCAL_FIXTURE_BOUNDARY.md](./LOCAL_FIXTURE_BOUNDARY.md)
- A-Eco 分析报告候选：[现役进度](./RELEASE_CANDIDATE_PROGRESS.md)／[当前阻断](./RELEASE_CANDIDATE_BLOCKED.md)／[PR、迁移与视觉复核](./RELEASE_CANDIDATE_REVIEW.md)／[本机交接](./TEST_HANDOFF.md)／[repo-relative 本地入口](./deploy/analysis-report/local_candidate.py)／[远端参数化命令单](./deploy/analysis-report/DEPLOYMENT.md)

## 下一步

`955a274` 前的本地组件证据保持原状；当前上传/分析/报告持久化 delivery、OCR checkpoint/修订、actor rebind、DB-only status、列级 DML/trigger 和前端状态差异已取得 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`。证据包括 23 项直接合同、隔离 `f1_0014→f1_0023` 迁移/47 表目录门、真实 Redis/worker 崩溃与队列丢失恢复、真实 PostgreSQL 加密 OCR checkpoint 恢复，以及使用 fixture 材料的当前浏览器 workflow UAT；Pydantic `schema` 字段命名 warning 已用内部字段+alias 修复，warnings-as-errors import 与序列化/OpenAPI `schema` 合同保持通过。人工视觉为 `NOT_PART_OF_THIS_RUN`；当前工作树仍是 `NOT_COMMITTED / NOT_PUSHED / NOT_PRODUCTION`——`f1_0023` 无 OCR 候选已于 2026-09-01 部署到测试服务器（`TEST_SERVER_DEPLOYED / REMOTE_NATIVE_TEXT_SMOKE_PASSED`，loopback+SSH 隧道），但部署物来自未提交工作树的仓外包，带 SHA 回执、不能对应到可审计 Git 提交。整体因历史 OIDC callback code 输出与收尾命令硬规则违规保持 `BLOCKED`。下一步按序：① 冻结当前 119 个工作树路径为可审计提交（待授权）；② 轮换服务器密码并绑定 SSH key；③ 远端回滚演练（新栈停→旧 `f1_0020` 起→readiness/数据核对）；④ 远端 Redis/worker 故障注入与恢复验证；⑤ 补业务级清理 API（清除 `ZZ-SYNTH-AUTO-*` 证据资料/草稿/MinIO 对象/索引）；完整 amd64 OCR runtime、扫描 PDF、DNS/TLS/edge/systemd 和生产发布继续单独立项阻断。之后再按授权推进 PR #3→#4；不是 `RELEASE_VERIFIED`，不进入生产或正式小程序，`pdf-inspector` 仍为 `RUNTIME_DISABLED`。
