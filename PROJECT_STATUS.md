# 项目现役状态

更新日期：2026-08-12
本页是当前状态的唯一项目级入口；阶段文档中的早期 `当前`、`READY` 或 `NOT_TESTED` 记录均按其日期保留，不覆盖本页。

## 代码与版本

- 开发 checkout：当前仓库根目录
- 分支：`codex/material-intake`（独立工程分支；是否已推送以 Git 远端为准）
- 干净基线：`origin/main@8d2e791b019ede7f1c3b5e939258952503bf7b89`
- 当前工程基线：`codex/engineering-closeout@69f6d41`，其上为材料录入切片
- F1 Alembic：`f1_0001 → … → f1_0011`，唯一源码 head 为 `f1_0011`
- 远端边界：只允许推送 `codex/material-intake` 工程分支；未部署、不写生产
- 旧 `codex/f1-1-1-repair` 只保留作历史证据，不再继续开发或推送

## 阶段状态

| 阶段 | 当前标签 | 已验证层 | 未开放边界 |
| --- | --- | --- | --- |
| 工程收口 | `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION` | 已保留 230/230、verify 五门、reset/restore、重启、浏览器/PWA 技术摘要 | 精确顺序的治理证据重放表仍为 pending；OS 级 PWA 安装因浏览器自动化边界未测；未 UAT、未生产 |
| F1.1.1 | `F1_1_1_PAUSED_NOT_ACCEPTED` | 历史修复与拒绝证据保留 | formal/reverse/SBOM/clean/M4 未恢复；tracked v0.3 仍为 `F1_1_1_REJECTED` |
| P2 | `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION` | 真实 API + FORCE RLS 主链、跨租户边界、非法关闭 409 与事务零漂移；真实 Keycloak 合成身份 | 真实客户资料、发布验收、生产 |
| P3 | `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL + MinIO + ClamAV 上传、扫描、预览、释放；MinIO 写失败与 ClamAV 不可用后恢复；跨租户 404 | 真实客户资料、生产容量、正式解析引擎 |
| P4 | `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的 CRM、报告快照主链 | 正式 PDF/HTML、签发、发布、真实客户数据 |
| P5 | `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的提交、独立审核、发布状态与影响任务 | 联网法规源、法律意见、外部发布 |
| P6 | `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 合成 Oracle 与分歧流程，外部调用为 0 | Gold、真实 OCR/LLM 准确率、发布门 |
| P7 | `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL/API/RLS 人工结果与回滚门；本地 PostgreSQL + MinIO 备份/恢复链 | 故障切换、部署或生产访问 |
| P8 | `P8_COMPLETE_NOT_RELEASE_VERIFIED / INTERNAL_PWA_ONLY / NOT_PRODUCTION` | 3 类 OIDC 身份；管理员 17、顾问 2、企业 2 页；离线静态壳与真实 A→B waiting update 用户确认链 | OS 级应用安装 `BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY / PWA_OS_INSTALL_NOT_TESTED`、设备矩阵、正式小程序发布 |
| 材料录入降本 | `SMOKE_PASSED / NOT_PRODUCTION` | `f1_0011` 实库迁移；一份合成文本 PDF 经进程内 ASGI HTTP/API 路由、真实 MinIO/ClamAV、预览、4候选、释放、P5来源+draft、幂等与跨租户 RLS；TypeScript `--noEmit` | 真实PDF/批量浏览器、OCR、准确率、备份恢复、P4报告自动创建、Inspector运行时 |

已保留的技术摘要为：直接相关检查 `230/230 OK`，备份 `20260810T224332Z-2a861bccbba9` 完成 `reset → restore`，恢复后 health ready、verify 五门全绿、浏览器与 PWA 更新链通过。这些摘要不替代当前 pending 的精确顺序重放证据表。P8 构建仍有单 JS 约 1.48 MiB 的非阻断性能债。

## 运行与发布边界

- 材料录入分支已在同一专属、双标签本地 Compose 栈真实迁移到 `f1_0011`，并在随机 scratch 数据库和随机 MinIO 桶验证一份合成 PDF；没有使用旧共享 `anhuan-f1` 栈作证。
- 当前专属栈已停止并保留数据卷；需要继续本地工作时再显式执行 `./scripts/localctl start`。
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
- 本地 Fixture 使用边界：[LOCAL_FIXTURE_BOUNDARY.md](./LOCAL_FIXTURE_BOUNDARY.md)

## 下一步

材料录入当前结论为 `SMOKE_PASSED / NOT_PRODUCTION`。下一步若继续，应建立代表性内部 PDF 准确率集与扫描件 OCR 路由，再单独验证批量浏览器流程；不恢复旧 F1.1.1 发布验收或 PWA OS 探针，不进入真实 UAT、生产或正式小程序。`pdf-inspector` 仍为 `RUNTIME_DISABLED`。
