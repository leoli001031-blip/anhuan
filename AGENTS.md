# 安环项目协作规则

本仓库是本地、非生产的安环运营平台原型。局部检查、随机 scratch 冒烟或前端构建都不等于发布验收。

## 现役权威

- 开工先读 [PROJECT_STATUS.md](./PROJECT_STATUS.md)，再读当前阶段的 `TASKBOOK / PROGRESS / BLOCKED`。
- 根 `PROGRESS.md`、`BLOCKED.md` 是历史流水账，不是现役状态来源。
- F1.1.1 保持 `F1_1_1_PAUSED_NOT_ACCEPTED`；不得自动恢复 formal、reverse、SBOM、clean rebuild 或 M4。
- P2-P8 的精确验证层级以 `PROJECT_STATUS.md` 为准；不得自动进入真实 UAT、生产或正式小程序发布。

## 修改边界

- 修改前检查 `git status --short --branch` 和 `git worktree list`，保留其他 worktree 的用户改动。
- Alembic 只允许一条线性 head；当前源码 head 为 `f1_0011`。迁移、models、API main、App/Layout、lockfile由单一执行者写。
- 复用现有租户上下文和 RLS；跨租户详情返回 404、集合零行；关键状态变化与 timeline/audit 同事务。
- 不修改历史 F0/F1 证据、REJECTED 批次或冻结原件，不清理共享数据库、对象、卷、容器或 secret 目录。
- 未经明确授权不 commit、push、deploy、删除文件或写生产。

## 目录与验证

- 后端：`src/platform_foundation/f1/`；迁移：`infra/f1/alembic/versions/`；前端：`src/web/`；直接检查：`tests/`。
- 前端开发命令位于 `src/web/package.json`；共享栈配置为 `infra/f1/docker-compose.yml`，不得自行启停。
- 默认遵循全局轻量原型预算；只报告实际达到的 `NOT_TESTED / SMOKE_PASSED / TARGETED_TEST_PASSED / RELEASE_VERIFIED`。
- 没有新授权时停在本地原型，不续做发布验收、部署或下一产品阶段。
