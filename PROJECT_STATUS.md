# 项目现役状态

更新日期：2026-08-11
本页是当前状态的唯一项目级入口；阶段文档中的早期 `当前`、`READY` 或 `NOT_TESTED` 记录均按其日期保留，不覆盖本页。

## 代码与版本

- 开发 worktree：`/private/tmp/anhuan-codex-engineering-closeout`
- 分支：`codex/engineering-closeout`
- 干净基线：`origin/main@8d2e791b019ede7f1c3b5e939258952503bf7b89`
- 最近已提交 checkpoint：`5db390e`；当前工程完成门证据已通过，最终文档/代码提交待主线写入
- F1 Alembic：`f1_0001 → … → f1_0010`，唯一源码 head 为 `f1_0010`
- 当前分支无 upstream/远端集成证据；未 push、未部署
- 旧 `codex/f1-1-1-repair` 只保留作历史证据，不再继续开发或推送

## 阶段状态

| 阶段 | 当前标签 | 已验证层 | 未开放边界 |
| --- | --- | --- | --- |
| 工程收口 | `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION` | 230/230 定向检查、统一 verify 五门、真实 reset/restore、真实重启、多角色浏览器与 PWA 更新链 | OS 级 PWA 安装 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`；未 UAT、未生产 |
| F1.1.1 | `F1_1_1_PAUSED_NOT_ACCEPTED` | 历史修复与拒绝证据保留 | formal/reverse/SBOM/clean/M4 未恢复；tracked v0.3 仍为 `F1_1_1_REJECTED` |
| P2 | `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION` | 真实 API + FORCE RLS 主链、跨租户边界、非法关闭 409 与事务零漂移；真实 Keycloak 合成身份 | 真实客户资料、发布验收、生产 |
| P3 | `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL + MinIO + ClamAV 上传、扫描、预览、释放；MinIO 写失败与 ClamAV 不可用后恢复；跨租户 404 | 真实客户资料、生产容量、正式解析引擎 |
| P4 | `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的 CRM、报告快照主链 | 正式 PDF/HTML、签发、发布、真实客户数据 |
| P5 | `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的提交、独立审核、发布状态与影响任务 | 联网法规源、法律意见、外部发布 |
| P6 | `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 合成 Oracle 与分歧流程，外部调用为 0 | Gold、真实 OCR/LLM 准确率、发布门 |
| P7 | `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | PostgreSQL/API/RLS 人工结果与回滚门；本地 PostgreSQL + MinIO 备份/恢复链 | 故障切换、部署或生产访问 |
| P8 | `P8_COMPLETE_NOT_RELEASE_VERIFIED / INTERNAL_PWA_ONLY / NOT_PRODUCTION` | 3 类 OIDC 身份；管理员 17、顾问 2、企业 2 页；管理员 API 92 次且非 2xx 为 0；离线静态壳与真实 A→B waiting update 用户确认链 | OS 级应用安装 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`、设备矩阵、正式小程序发布 |

最终直接相关检查 `230/230 OK`。备份 `20260810T224332Z-2a861bccbba9` 已完成真实 `reset → restore`；恢复后 health ready，统一 `localctl verify` 五门全绿，浏览器与 PWA 更新链再次通过。P8 构建仍有单 JS 约 1.48 MiB 的非阻断性能债。

## 运行与发布边界

- 当前权威运行态是 `codex/engineering-closeout` worktree 的独立本地 Compose 栈；只有 Web 绑定随机 loopback 端口。
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
- PDF Inspector 决策：[PDF_INSPECTOR_INTEGRATION_DECISION.md](./PDF_INSPECTOR_INTEGRATION_DECISION.md)
- 本地 Fixture 使用边界：[LOCAL_FIXTURE_BOUNDARY.md](./LOCAL_FIXTURE_BOUNDARY.md)

## 下一步

工程完成门已通过，当前结论为 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。下一步只是写入最终本地提交并向内部团队交接；不自动 push、部署、进入真实 UAT、生产或正式小程序。OS 级 PWA 安装仍为 `DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_TESTED`。
