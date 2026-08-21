# Engineering Closeout Taskbook

> **历史范围说明：** 本任务书冻结的是 `codex/engineering-closeout` 当轮范围。2026-08-12 获明确授权的新材料录入切片由 `MATERIAL_INTAKE_TASKBOOK.md` 单独治理，并线性新增 `f1_0011`；这不改写本任务书当时的 `f1_0010`/31表完成门。

目标：把 P2–P8 本地原型收成一个人可启动、维护、恢复和用真实浏览器操作的工程。完成标签只能是：

```text
INTERNAL_ENGINEERING_READY
NOT_PRODUCTION
```

当前状态为 `TECHNICAL_ENGINEERING_READY / GOVERNANCE_CLOSEOUT_PENDING / NOT_PRODUCTION`。已有技术证据保留，但在文件地界、轮次和最终证据序列可审计前，不得恢复上述完成标签。

优先级：数据与租户安全 > 可恢复 > 真实链路 > 易用 > 速度。

## 固定边界

- 开发分支固定 `codex/engineering-closeout`，根提交固定 `8d2e791b019ede7f1c3b5e939258952503bf7b89`。
- 本地旧 `main`、`codex/f1-1-1-repair` 和 PDF Probe 只读；禁止合并、rebase、cherry-pick 或复制旧历史。
- F1 唯一 head 保持 `f1_0010`；不新增业务表，不修改 `f1_0001` 至 `f1_0010`。
- 不恢复 F1.1.1 formal、reverse、SBOM、clean rebuild 或 M4；不做全仓 discover、生产部署或正式小程序发布。
- 不清理共享数据库、对象、容器、卷或 secret；本轮只操作带当前 project-id 与 label 的资源。
- 所有 active secret 使用当前用户拥有的 regular 0600 文件，父目录 0700；不得写入仓库、环境默认值、argv、日志或浏览器存储。

## 允许文件地界与共享文件单写者

本地界是后续修复和证据重放的前置合同。地界外文件必须先获得用户明确授权并在本书记录，不得在实现后再修改合同追认。

| 类别 | 允许改/建 |
| --- | --- |
| 治理与运维 Markdown | `ENGINEERING_CLOSEOUT_{TASKBOOK,PROGRESS,BLOCKED}.md`、`PROJECT_STATUS.md`、`LOCAL_OPERATIONS.md`、`TROUBLESHOOTING.md`、`RECOVERY.md`、`PDF_INSPECTOR_INTEGRATION.md` |
| 底座控制 | `.gitignore`、`scripts/localctl`、`infra/f1/docker-compose.local.yml`、`infra/f1/local.Dockerfile`、`infra/f1/*.Dockerfile.dockerignore`、`infra/f1/local/**`、`infra/f1/local_*.py`、`infra/f1/keycloak/realm-local.json` |
| 已有 F1 集成接缝 | `infra/f1/alembic/env.py`、`infra/f1/migrate_f1.py`、`infra/f1/nginx/default.conf`、`infra/f1/web.Dockerfile`、`src/platform_foundation/f1/{health.py,database.py,keycloak_provision.py,observability.py,worker_pipeline.py}`、`src/platform_foundation/f1/api/{main.py,routers/service_cases.py}`、`src/platform_foundation/f1/features/p3/{processor.py,service.py}` |
| 前端集成接缝 | `src/web/public/pwa-sw.js`、`src/web/scripts/{engineering-browser-verify.mjs,inject-pwa-build-id.mjs}`、`src/web/src/{App.tsx,api.ts}`、`src/web/src/auth/{OidcProvider.tsx,oidcConfig.ts,userManager.ts}`、`src/web/src/features/p8/**`、`src/web/src/pages/{AdminPage.tsx,AuditPage.tsx,DocumentList.tsx,EnterpriseList.tsx,InvitePage.tsx,Layout.tsx,QAPage.tsx}` |
| 直接检查 | `tests/test_engineering_closeout_*.py`、`tests/test_p2_wave{1,2,3,4}.py`、`tests/test_p3_controlled_ingestion.py`、`tests/test_p8_internal_pwa.py` |

以下始终只读且必须相对基线保持零 diff：根 `.dockerignore`、`tests/test_f111_repair_keycloak.py`、全部 F0 文件、`infra/f1/alembic/versions/f1_0001*.py` 至 `f1_0010*.py`、requirements/lock/Cargo 依赖文件、旧 worktree/历史证据、共享数据与资源。

### 已发生的范围偏差（如实保留，不追认）

后续真实浏览器和定向运行镜像收口中，以下文件在用户授权工程重放后被修改或新增，但没有在上表中事前列出：

- `src/web/src/components/AssignmentDrawer.tsx`
- `src/web/src/features/p3/pages/DocumentLibraryPage.tsx`
- `src/web/src/pages/ServiceCaseDetail.tsx`
- `infra/f1/local-targeted.Dockerfile`
- `infra/f1/local-tsc`

前三项只增加稳定测试接缝或完整错误/重试状态，后两项提供无额外依赖的定向检查镜像入口。它们属于已保留技术主链，不回滚；但本记录不把事后事实改写成事前合规，工程总状态继续保持 `GOVERNANCE_CLOSEOUT_PENDING`。

主 agent 负责为每个共享文件集预先指定唯一写者，默认写者是主 agent；也可在执行前把一个完整、不重叠的文件集显式交给一个 subagent。共享文件包括：三份工程收口治理文档、`scripts/localctl`、`infra/f1/local_*.py`、Compose/Docker 控制文件、migration env/helper、`src/platform_foundation/f1/{health.py,database.py,keycloak_provision.py}`、API `main.py`、前端 `App.tsx`/`api.ts`/OIDC/Layout 入口及可写的 `.gitignore`。根 `.dockerignore` 仍属上段冻结文件，不得因“共享文件”表述解冻。同一文件不得并发写，交付时必须在 `ENGINEERING_CLOSEOUT_PROGRESS.md` 记录唯一写者和文件集。

## 交付顺序

1. `./scripts/localctl`：start、stop、health、migrate、seed、reset、backup、restore、verify。
2. 自带 PostgreSQL、Keycloak、MinIO、Redis、ClamAV、API、worker、dispatcher、web 的独立本地栈；不依赖固定端口、旧共享栈、用户 HOME、外部 provider 或 RAGFlow。
3. 空库到 `f0d_0006/f1_0010`、重复迁移、失败原子性、31 张 P2–P7 业务表 FORCE RLS、真实低权限角色与跨租户边界。
4. 同一环境完成 P2–P7 主链、幂等、重启恢复、对象/扫描故障、非法状态跳转和事务回滚。
5. 真实 Keycloak/OIDC 浏览器链覆盖管理员、顾问、企业角色及 P2–P8 页面；权限按钮只来自 `allowed_actions`。
6. Service Worker 首次安装后的离线静态壳、敏感请求拒缓存、用户确认更新和本应用前缀精确清理；macOS OS 级安装不属于本轮完成门。
7. PostgreSQL 备份恢复、MinIO 对象身份检查、精确 reset、日志泄漏门和三份本地运维文档。
8. PDF Inspector 仅形成受控集成决策；当前 `0.2.6/lopdf 0.41` 不得进入 API 或任意上传主链。

## PDF Inspector 决策门

- 本轮只交付 [PDF_INSPECTOR_INTEGRATION.md](./PDF_INSPECTOR_INTEGRATION.md)，状态固定为 `ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`；不改 requirements、lock、Compose、API、worker、migration 或页面。
- 旧 PDF Probe 仅作为只读设计证据，不合并源码或历史；其 Fixture shadow 结论不等于准确率或任意上传可用。
- 未来仅允许 patched pinned build 在 P3 源身份匹配且 ClamAV `clean` 后，以默认 OFF、进程外、无网络、无 secret、资源受限 shadow 运行。
- `pypdf` 保持权威解析和 fallback；Inspector 结果只能是人工确认前的草稿，不得直接进入证据、索引、报告、法规、RAG/QA、OCR 路由或外部通知。
- 任一供应链、隔离、跨租户、正文泄漏或权威覆盖门不通过时保持 OFF；启用必须另开任务书并单独授权。

## 完成门

- 从空状态严格按以下顺序完成：`test → reset --confirm-local-data → start → migrate → migrate → seed → health --json → verify → dependency-verify → stop → start → health --json → verify → backup → reset --confirm-local-data --prove-foreign-sentinel → restore --backup-id <id> --confirm-local-data → health --json → verify → browser-verify → health --json → stop`。
- P2–P8 定向测试不少于 137 项，失败 0、跳过 0；唯一 F1 head 为 `f1_0010`。
- 31 张业务表全部 ENABLE + FORCE RLS；跨租户 API 泄漏、RLS 读写泄漏、事务缺口、对象假状态、缓存泄漏、secret/log 泄漏、共享资源变更和本轮残留全部为 0。
- 每项保留真实命令输出与故障反测的红→绿证据；只报告实际达到的状态。

## 最多 12 轮与证据记录合同

- 从本次治理门重开起，最多执行 12 轮。“一轮”是一次预先登记的假设/目标、一组严格文件地界、一次最小执行和一条聚合结果。
- 每轮开始前在 Progress 表中登记轮号、目标、允许文件和预期固定证据；结束后补退出码、固定标签和状态。没有记录的执行不得倒推成成功轮次。
- 同一原因连续失败 3 次时必须更换方案，并在下一轮记录方案变更。到达 12 轮仍未闭合时停止，状态保持 `GOVERNANCE_CLOSEOUT_PENDING`。
- 历史实现没有可靠的逐轮编号，不得事后伪造或补写其结果。现役轮次表从治理修正 G1 开始。

最终证据表每行必须含：顺序号、实际命令（不含 secret）、开始/结束时间、执行 commit、退出码、固定输出/聚合计数、残留计数和状态。模板在 `ENGINEERING_CLOSEOUT_PROGRESS.md`；未真实重放的行一律保持 `PENDING_REPLAY_EVIDENCE`。
