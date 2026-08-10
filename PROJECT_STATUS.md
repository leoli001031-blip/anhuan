# 项目现役状态

更新日期：2026-08-11
本页是当前状态的唯一项目级入口；阶段文档中的早期 `当前`、`READY` 或 `NOT_TESTED` 记录均按其日期保留，不覆盖本页。

## 代码与版本

- 开发 worktree：`/private/tmp/anhuan-codex-f111-repair`
- 分支：`codex/f1-1-1-repair`
- 最近代码/证据 checkpoint：`9d712cd`（完整 SHA `9d712cdd0345136fd0ec6422cb0e05eae51b8e9d`）
- F1 Alembic：`f1_0001 → … → f1_0010`，唯一源码 head 为 `f1_0010`
- 当前分支无 upstream/远端集成证据；未 push、未部署、未合入只读 main worktree
- 本次 neat-freak 文档收口尚未 commit

## 阶段状态

| 阶段 | 当前标签 | 已验证层 | 未开放边界 |
| --- | --- | --- | --- |
| F1.1.1 | `F1_1_1_PAUSED_NOT_ACCEPTED` | 历史修复与拒绝证据保留 | formal/reverse/SBOM/clean/M4 未恢复；tracked v0.3 仍为 `F1_1_1_REJECTED` |
| P2 | `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION` | 79 项定向检查；随机 PostgreSQL + ASGI API + FORCE RLS 主链 | 真实 Keycloak/OIDC、发布验收、生产 |
| P3 | `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL + MinIO + ClamAV 的上传、扫描、预览、释放与跨租户 404 | 真实客户资料、生产容量、浏览器链 |
| P4 | `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的 CRM、报告快照主链 | 正式 PDF/HTML、签发、发布、真实客户数据 |
| P5 | `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的提交、独立审核、发布状态与影响任务 | 联网法规源、法律意见、外部发布 |
| P6 | `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 合成 Oracle 与分歧流程，外部调用为 0 | Gold、真实 OCR/LLM 准确率、发布门 |
| P7 | `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION` | 随机 PostgreSQL/API/RLS 的人工结果与回滚门 | 未执行 Docker 恢复、故障切换、shell、部署或生产访问 |
| P8 | `P8_COMPLETE_NOT_RELEASE_VERIFIED / TARGETED_TEST_PASSED / INTERNAL_PWA_ONLY / NOT_PRODUCTION` | 6 项定向检查与真实 Vite 生产构建产物检查 | 浏览器安装/离线 E2E、设备矩阵、视觉回归、正式小程序发布 |

P3-P8 联合定向回归记录为 `58/58 OK`。P8 构建仍有单 JS 约 1.48 MiB 的非阻断性能债；它不能标为 `SMOKE_PASSED`。

## 运行与发布边界

- 上述实库证据来自已精确清理的随机 scratch，不是常驻或生产运行态。
- 现有共享 `anhuan-f1` Docker 栈不属于 P2-P8 验收权威，禁止据其存在声明系统已部署或可生产使用。
- 未执行真实 UAT、生产部署、生产数据迁移、正式小程序发布或客户数据验证。
- 没有新授权时不自动进入下一阶段，也不恢复 F1.1.1 发布验收。

## 文档入口

- F1.1.1 暂停事实：[F1_1_1_REPAIR_PROGRESS.md](./F1_1_1_REPAIR_PROGRESS.md)
- P2：[P2_BUSINESS_WORKBENCH_PROGRESS.md](./P2_BUSINESS_WORKBENCH_PROGRESS.md)
- P3：[P3_CONTROLLED_INGESTION_PROGRESS.md](./P3_CONTROLLED_INGESTION_PROGRESS.md)
- P4：[P4_VIEWS_REPORTS_CRM_PROGRESS.md](./P4_VIEWS_REPORTS_CRM_PROGRESS.md)
- P5：[P5_POLICY_WORKFLOW_PROGRESS.md](./P5_POLICY_WORKFLOW_PROGRESS.md)
- P6：[P6_AUTOMATED_QUALITY_PROGRESS.md](./P6_AUTOMATED_QUALITY_PROGRESS.md)
- P7：[P7_LOCAL_PRODUCTION_REHEARSAL_PROGRESS.md](./P7_LOCAL_PRODUCTION_REHEARSAL_PROGRESS.md)
- P8：[P8_INTERNAL_PWA_PROGRESS.md](./P8_INTERNAL_PWA_PROGRESS.md)
- 本地 Fixture 使用边界：[LOCAL_FIXTURE_BOUNDARY.md](./LOCAL_FIXTURE_BOUNDARY.md)

## 下一步

当前没有自动执行中的产品阶段。只有用户明确说“正常验证”时才运行直接相关检查；只有明确说“发布验收”或“完整回归”时才考虑扩大验证。commit、push、部署、清理共享数据或移除 worktree 均需单独授权。
