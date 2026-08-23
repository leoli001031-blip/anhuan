# 项目现役状态

更新日期：2026-08-24（A-Eco 分析报告本地候选组件门与部署包已收口；整体证据 BLOCKED；人工、远端、提交、合并与部署均 pending）
本页是当前状态的唯一项目级入口；阶段文档中的早期 `当前`、`READY` 或 `NOT_TESTED` 记录均按其日期保留，不覆盖本页。

## 代码与版本

- 当前候选 checkout：`/Users/lichenhao/Desktop/安环项目/material-rag-analysis-report-visual-polish-20260823/source/repo`，分支 `codex/material-report-aeco-polish@1ea4fe3161f94b0397b2016f50571a1b18bf2250`。
- PR 栈：#3=`main@dd466e5 → codex/material-report-integration@6cdbba3`；#4=`codex/material-report-integration@6cdbba3 → codex/material-report-aeco-polish@1ea4fe3`。两者均 `OPEN / draft / MERGEABLE / statusCheckRollup=[]`；这是无 CI 门禁，不是 CI 通过。
- 合并顺序固定为 #3 先入 `main`；随后把 #4 base 改为 `main` 并核差异只剩本层。用户已授权当前候选做本地 commit；仍未授权 push、改 PR、mark ready、merge 或 deploy。
- PR #2 已于 2026-08-21 合并，合并时 head 为 `codex/material-rag-postgres-integration@af0d74470a81275a64be08638d7272197bd53095`；原“OPEN+draft”说法已经退役。其 worktree 仅保留历史/并行工程现场，不是当前候选 checkout。
- F1 Alembic：源码单一 head 为 `f1_0018`，线性顺序含 `f1_0016 → f1_0017 → f1_0018`；`migrate_f1` 目标闭集为 `{f1_0014,f1_0015,f1_0016,f1_0017,f1_0018}`。默认工程仍锁 `f1_0014`，material-RAG 专属目标为 `f1_0016`，analysis-report 专属目标为 `f1_0018`；默认 seed/verify/backup 的 0014/35 合同保持不变。
- 分析报告能力：只在专属本地 demo 以 `tenant-a` / `invitee` 合成身份、`deterministic_local`、`ark_calls=0`、`mock_data=0` 取证；不是正式评分器、生产 worker、真实 Ark 或客户数据能力。
- 远端边界：Netlify 静态前端 + 单一 HTTPS edge 的参数化部署/回滚/smoke 已本地整合，但没有真实 DNS/TLS/Keycloak/CORS/PG 参数，未连接、未部署、未生产。
- 旧 `codex/f1-1-1-repair` 只保留作历史证据，不再继续开发或推送

## 阶段状态

| 阶段 | 当前标签 | 已验证层 | 未开放边界 |
| --- | --- | --- | --- |
| A-Eco 分析报告候选 | `BLOCKED / VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / COMMITTED_LOCAL / NOT_PUSHED / NOT_PRODUCTION` | 19项部署 preflight、7页双视口14图、真实 UI 主流程、workflow UAT、lint/build/diff-check；全部为本地合成身份与测试生成器 | 历史 OIDC code 输出与收尾命令规则违规未裁决；无 CI、未人工签字、未 push/合并、无远端参数、未部署 |
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

- 分析报告专属 demo 当前已停止，专属 containers/running/volumes/networks/control directory 均为 0；`src/web/dist`、临时 `src/web/node_modules` symlink 与根 `netlify.toml` 均不存在。仓外最终截图与机器原始证据继续保留为 0700/0600，不是当前 live 服务。
- 材料录入分支已在专属、双标签本地 Compose 栈从实库 `f1_0011` 迁移到 `f1_0014`，并在随机 scratch 数据库和随机 MinIO 桶将同一份合成 PDF 分别按服务公司域、客户域验证。第一次迁移因 `FORCE RLS` 遮蔽旧文档回填而失败且事务整体回滚；限定 bootstrap session 的有界 `RESET ROLE` 回填修复后重跑得到 `LOCAL_MIGRATE_OK`。提交前又以 `f1_0014` 收紧底层原件/受控任务读取并完成同一验证。没有使用旧共享 `anhuan-f1` 栈作证。
- 默认工程 closeout 栈目前停止且保留数据卷：本机可见 9 个 `anhuan-closeout-*` 容器全部 Exited，对应卷仍在。material-RAG 最近一次重放的专属 container、volume、network、runtime image 残留为 0。两者不是同一运行环境。
- 宿主另有桌面 checkout 的共享 `anhuan-f1`（本窗口实测 15 容器全部 exited）和历史 `anhuan-f0d`。本窗口未启停任何共享容器。共享栈仍不是索引、检索或工程完成证据。
- 真实 `stop → start` 会强制重建 9 个核心容器但保留卷；重启后数据库、业务行和统一 verify 五门仍通过。
- scratch 数据库和 P3 临时对象只用于 verifier，每轮结束后精确删除。旧共享 `anhuan-f1` 栈不是本轮工程完成证据。
- 未执行真实 UAT、生产部署、生产数据迁移、正式小程序发布或客户数据验证。
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
- A-Eco 分析报告候选：[现役进度](./RELEASE_CANDIDATE_PROGRESS.md)／[当前阻断](./RELEASE_CANDIDATE_BLOCKED.md)／[PR、迁移与视觉复核](./RELEASE_CANDIDATE_REVIEW.md)／[本机交接](./TEST_HANDOFF.md)／[远端参数化命令单](./deploy/analysis-report/DEPLOYMENT.md)

## 下一步

当前候选只能停在本地组件级通过：整体仍因历史 OIDC callback code 输出与收尾命令硬规则违规保持 `BLOCKED`。后续若另行授权，先由发布负责人裁决两项过程例外，再按 PR #3→#4 顺序推进；#3 合入后必须把 #4 base 改为 `main` 并复核。之后仍需人工视觉签字、真实远端参数、备份点、`f1_0017→f1_0018` 专属迁移与双身份 remote smoke。PR #2 已合并；material-RAG 的 Ark live、正式用户 restore apply、断电验证和领导 UAT 仍各自 pending。不是 `RELEASE_VERIFIED`，未部署，不进入生产或正式小程序；`pdf-inspector` 仍为 `RUNTIME_DISABLED`。
