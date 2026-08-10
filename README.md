# 安环运营平台（本地原型）

这是一个面向环保托管运营的多租户本地原型，当前代码覆盖服务任务、受控文档进入、业务驾驶舱与内部 CRM、政策审核、合成质量、本地演练以及内部 PWA 壳。

项目目前是 `NOT_RELEASE_VERIFIED / NOT_PRODUCTION`。现役事实、验证证据和未开放边界统一见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)；根 [PROGRESS.md](./PROGRESS.md) 与 [BLOCKED.md](./BLOCKED.md) 仅保留历史过程。

## 功能地图

- P2：服务任务、分配、现场服务、整改复核、时间线、日历与提醒。
- P3：版本化文档、quarantine、本地扫描、安全预览与人工释放。
- P4：经营驾驶舱、内部 CRM、报告不可变快照与版本元数据。
- P5：政策来源、内部审核发布状态、影响候选与任务。
- P6：合成 Oracle、质量 run、结果与人工分歧处置。
- P7：本地人工计划、检查快照、结果与回滚门；不执行部署。
- P8：内部可安装 PWA 静态壳；不缓存业务 API 数据，不发布正式小程序。

## 代码入口

- FastAPI 与业务功能：`src/platform_foundation/f1/`
- F1 线性迁移：`infra/f1/alembic/versions/`
- React 前端：`src/web/`
- 阶段定向检查与 scratch runners：`tests/`
- 本地栈配置：`infra/f1/docker-compose.yml`

前端开发：

```bash
cd src/web
npm ci
npm run dev
```

后端运行依赖显式的本地 secret 文件、数据库和身份服务配置；不要把共享栈、历史凭据或旧验收环境当作默认开发环境。需要运行验证时，先按 [AGENTS.md](./AGENTS.md) 的预算与边界执行。

## 明确未开放

真实客户数据、客户 UAT、生产部署、正式报告签发、法律意见、准确率/Gold 结论、真实生产演练、外部通知和正式小程序发布均未开放。
