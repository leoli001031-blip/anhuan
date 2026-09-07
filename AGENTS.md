# 安环项目协作规则

本仓库是本地、非生产的安环运营平台原型。局部检查、随机 scratch 冒烟或前端构建都不等于发布验收。

## 现役权威

- 开工先读 [PROJECT_STATUS.md](./PROJECT_STATUS.md)，再读当前阶段的 `TASKBOOK / PROGRESS / BLOCKED`。
- A-Eco 分析报告候选的现役入口依次为 `RELEASE_CANDIDATE_PROGRESS.md` 末尾事实矩阵、`RELEASE_CANDIDATE_BLOCKED.md`、`RELEASE_CANDIDATE_REVIEW.md`；前文阶段快照和根历史流水账不得覆盖它们。
- 根 `PROGRESS.md`、`BLOCKED.md` 是历史流水账，不是现役状态来源。
- F1.1.1 保持 `F1_1_1_PAUSED_NOT_ACCEPTED`；不得自动恢复 formal、reverse、SBOM、clean rebuild 或 M4。
- P2-P8 的精确验证层级以 `PROJECT_STATUS.md` 为准；不得自动进入真实 UAT、生产或正式小程序发布。

## 修改边界

- 修改前检查 `git status --short --branch` 和 `git worktree list`，保留其他 worktree 的用户改动。
- Alembic 只允许一条线性源码 head，当前为 `f1_0026`；`migrate_f1` 目标闭集为 `f1_0014..f1_0026`。默认工程/verify/seed/backup 仍锁 `f1_0014 / 35`，material-RAG 专属目标为 `f1_0016`，只有 analysis-report 专属 migrator 请求 `f1_0026`。不得把专属目标写回默认栈；分析报告远端迁移只能在精确 `f1_0017` 的 pre-0023 备份点上线性前向到 `f1_0026`，失败走恢复式回滚，不做 downgrade。`f1_0025` 的 analysis_report guard 为三分支（归档/恢复/业务）：已归档或恢复中的报告仅允许归档列变化，混入业务字段即拒；归档状态下 submit/return/approve/publish 与两条 generate 派发路径（新请求 + 精确请求恢复/重投）均在报告行锁下拒绝（withdraw 例外为契约行为）。`f1_0026` 的 `analysis_report_management_event` 是归档/恢复的只插审计流（f1_api 无 UPDATE/DELETE 权限），事件插入 guard 要求事务身份绑定——报告行的 xmin 必须等于当前事务 ID（`report.xmin = pg_current_xact_id()` 掩码到 32 位），且同一事务对同一报告/动作只允许一条事件；时间戳先后（如 `updated_at>=transaction_timestamp()`）不构成同事务证明。注意旧版本审计 guard（f1_0023）仍用较弱的时间戳比较，属已登记的待修项。
- 复用现有租户上下文和 RLS；跨租户详情返回 404、集合零行；关键状态变化与 timeline/audit 同事务。
- 不修改历史 F0/F1 证据、REJECTED 批次或冻结原件，不清理共享数据库、对象、卷、容器或 secret 目录。
- 未经明确授权不 commit、push、deploy、删除文件或写生产。

## 目录与验证

- 后端：`src/platform_foundation/f1/`；迁移：`infra/f1/alembic/versions/`；前端：`src/web/`；直接检查：`tests/`。
- 前端开发命令位于 `src/web/package.json`；共享栈配置为 `infra/f1/docker-compose.yml`，不得自行启停。
- 默认遵循全局轻量原型预算；只报告实际达到的 `NOT_TESTED / SMOKE_PASSED / TARGETED_TEST_PASSED / RELEASE_VERIFIED`。
- `statusCheckRollup=[]` 只能写“无 CI checks”，不得写“CI 通过”；本地组件门、人工验收、PR 合并、远端部署与生产必须分开记状态。
- 没有新授权时停在本地原型，不续做发布验收、部署或下一产品阶段。
