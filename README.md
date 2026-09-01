# 安环运营平台（本地原型）

这是一个面向环保托管运营的多租户本地原型，当前代码覆盖服务任务、受控文档进入、业务驾驶舱与内部 CRM、政策审核、合成质量、本地演练以及内部 PWA 壳。

项目目前是 `NOT_RELEASE_VERIFIED / NOT_PRODUCTION`。A-Eco 候选本地 HEAD 为 `codex/material-report-aeco-polish@955a274990cd37797dbb6ef2c11459b288074ff8`；当前工作树为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`。这里的 `NOT_DEPLOYED` 精确指当前 `f1_0023` 工作树；服务器上的历史 `f1_0020` demo 为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。项目级现役入口是 [PROJECT_STATUS.md](./PROJECT_STATUS.md)；当前 A-Eco 分析报告候选另以 [RELEASE_CANDIDATE_PROGRESS.md](./RELEASE_CANDIDATE_PROGRESS.md)、[RELEASE_CANDIDATE_BLOCKED.md](./RELEASE_CANDIDATE_BLOCKED.md) 与 [RELEASE_CANDIDATE_REVIEW.md](./RELEASE_CANDIDATE_REVIEW.md) 保存历史机器门、阻断和 PR/迁移边界。根 [PROGRESS.md](./PROGRESS.md) 与 [BLOCKED.md](./BLOCKED.md) 仅保留历史过程。

## 功能地图

- P2：服务任务、分配、现场服务、整改复核、时间线、日历与提醒。
- P3：版本化文档、quarantine、本地扫描、安全预览与人工释放。
- P4：经营驾驶舱、内部 CRM、报告不可变快照与版本元数据。
- P5：政策来源、内部审核发布状态、影响候选与任务。
- P6：合成 Oracle、质量 run、结果与人工分歧处置。
- P7：本地人工计划、检查快照、结果与回滚门；不执行部署。
- P8：内部可安装 PWA 静态壳；不缓存业务 API 数据，不发布正式小程序。
- A-Eco 分析报告候选：普通 PDF 文本提取与客户授权问答、异步证据报告、7 节 HTML 产物、可追溯审核记录、发布/撤回、客户安全服务摘要与客户阅读；`evidence_local` 仅为本地证据生成能力，当前没有可信的健康度评分器，因此健康度保持“暂不评分”，不写入合成分数快照。
- 当前未提交的 `f1_0023` 工作树新增上传摄取、分析后处理和报告 generation 的数据库 delivery/outbox；报告 job/version 与 outbox 同事务注册，由 DB dispatcher + dispatch-token fence 恢复投递，状态只以数据库为准。逐页 OCR checkpoint、失败新修订、历史 actor rebind，以及报告/version/job/content/audit/review-evidence/health-snapshot 的列级 DML + 状态/发布/可见性/证据 trigger 也已进入工作树。当前证据为：23 项直接合同、真实隔离 PostgreSQL `f1_0014→f1_0023` 与 47 表 FORCE-RLS 目录；真实 Redis unavailable/恢复、worker SIGKILL、空队列重建、stale token 拒绝与 PostgreSQL 恢复；真实 PostgreSQL 加密 OCR checkpoint 跨进程恢复/过期清理；当前浏览器 workflow UAT exit=0、111.933 秒并输出 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`。Pydantic `schema` 字段命名 warning 已修；状态为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`。
- 证据边界：macOS runtime 使用独立 OS 子进程中的 RQ `SimpleWorker`，不是 Linux 容器 `Worker.main` 启动证明；OCR 使用 synthetic adapter，不证明识别准确率；浏览器 UAT 使用预置 eligible fixture，不证明上传→OCR→索引→报告全链。残余工程风险还包括共享的 `f1_api` API/report-worker 角色、数据库无法独立重算健康评分、ingestion worker 的 `f1_api` + MinIO root、DB finalize 失败后的 MinIO orphan、分析/索引重复 OCR、未自动处理的非 latest 历史版本，以及 Redis 长期故障时报告 delivery 会持续重试但 job status 尚未投影 delivery attempt/reason。
- Chrome/Tencent TAT 只读预检确认服务器为 `x86_64/amd64`、2 vCPU/4 GiB、Ubuntu 24.04、Docker 29、Compose 2.40；当时内存约 2.6 GiB 已用/1.0 GiB 可用、空闲 swap 约 1.4 GiB、磁盘可用约 47 GiB，未绑定 SSH key。服务器已有 Compose project `anhuan-ar-demo-0c74bf2a11ee`：10 个容器 healthy 约 20 小时，web=`127.0.0.1:58103`，health/ready 正常，数据库 head=`f1_0020`；但该栈缺 0021/0022/0023、关键文件哈希与当前工作树不同且没有 OCR container，只能标为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。当前 OCR 镜像仅 ARM64；仓库已补 amd64 无 OCR/native-text PDF 候选模式并生成未上传的仓外源码包。上传、停旧栈和 remote smoke 尚未执行，故当前 `f1_0023` 仍为 `NOT_DEPLOYED`。

## 代码入口

- FastAPI 与业务功能：`src/platform_foundation/f1/`
- F1 线性迁移：`infra/f1/alembic/versions/`
- React 前端：`src/web/`
- 阶段定向检查与 scratch runners：`tests/`
- 本地栈配置：`infra/f1/docker-compose.yml`
- 分析报告服务器交接模板：`deploy/analysis-report/`

前端开发：

```bash
cd src/web
npm ci
npm run dev
```

后端运行依赖显式的本地 secret 文件、数据库和身份服务配置；不要把共享栈、历史凭据或旧验收环境当作默认开发环境。需要运行验证时，先按 [AGENTS.md](./AGENTS.md) 的预算与边界执行。

A-Eco 分析报告的 repo-relative 本地入口（使用已安装本仓运行依赖的 Python；如果未激活该环境，通过 `A_ECO_PYTHON` 指定解释器）：

```bash
python -B deploy/analysis-report/local_candidate.py start
python -B deploy/analysis-report/local_candidate.py check --origin http://127.0.0.1:<port>
python -B deploy/analysis-report/local_candidate.py stop
```

`start` 复用 `scripts/localctl` 的专属 demo 生命周期与 `infra/f1/analysis-reports/migrate.py` 的 `f1_0023` 迁移门，并另外要求 `/api/readyz` 返回精确组件闭集。当前 `f1_0023` 工作树为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`，人工视觉为 `HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN`；标签仅覆盖上述本地合同、恢复门和 fixture 边界内的浏览器 workflow UAT，不是远端 E2E 或发布验收。服务器上的历史 `f1_0020` demo 不改变当前候选的 `NOT_DEPLOYED`；该入口不会升级或替换远端栈，远程参数化边界见 [deploy/analysis-report/DEPLOYMENT.md](./deploy/analysis-report/DEPLOYMENT.md)。

## 明确未开放

真实客户数据、客户 UAT、生产部署、正式报告签发、法律意见、准确率/Gold 结论、真实生产演练、外部通知和正式小程序发布均未开放。
