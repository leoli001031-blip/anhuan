# 安环运营平台（本地原型）

这是一个面向环保托管运营的多租户本地原型，当前代码覆盖服务任务、受控文档进入、业务驾驶舱与内部 CRM、政策审核、合成质量、本地演练以及内部 PWA 壳。

项目目前是 `NOT_RELEASE_VERIFIED / NOT_PRODUCTION`。全部开发线已合入 `main`（PR #1–#5，审计后 main 包含完整候选）；F1 迁移单一 head 为 `f1_0024`。测试服务器上运行 `f1_0024` 云 OCR 栈（loopback + SSH 隧道）：扫描件经 GLM-5.3-Flash 识别、GLM 生成分析报告、`artifact.pdf` 确定性下载，远端浏览器全流程 UAT 通过。项目级现役入口是 [PROJECT_STATUS.md](./PROJECT_STATUS.md)；当前 A-Eco 分析报告候选另以 [RELEASE_CANDIDATE_PROGRESS.md](./RELEASE_CANDIDATE_PROGRESS.md)、[RELEASE_CANDIDATE_BLOCKED.md](./RELEASE_CANDIDATE_BLOCKED.md) 与 [RELEASE_CANDIDATE_REVIEW.md](./RELEASE_CANDIDATE_REVIEW.md) 保存历史机器门、阻断和 PR/迁移边界。根 [PROGRESS.md](./PROGRESS.md) 与 [BLOCKED.md](./BLOCKED.md) 仅保留历史过程。

## 功能地图

- P2：服务任务、分配、现场服务、整改复核、时间线、日历与提醒。
- P3：版本化文档、quarantine、本地扫描、安全预览与人工释放。
- P4：经营驾驶舱、内部 CRM、报告不可变快照与版本元数据。
- P5：政策来源、内部审核发布状态、影响候选与任务。
- P6：合成 Oracle、质量 run、结果与人工分歧处置。
- P7：本地人工计划、检查快照、结果与回滚门；不执行部署。
- P8：内部可安装 PWA 静态壳；不缓存业务 API 数据，不发布正式小程序。
- A-Eco 分析报告候选：普通 PDF 与扫描件（云视觉 OCR：`glm_vision`/`ark_vision` 双 provider，chat/anthropic 方言，fail-closed）文本提取、客户授权问答、异步报告（`evidence_local` 确定性生成或 opt-in 的 GLM 生成 `glm_chat`，引用白名单约束）、HTML 与确定性 PDF 双产物、可追溯审核记录、发布/撤回、客户安全服务摘要与阅读；无可信健康度评分器时健康度保持“暂不评分”。上传/分析/报告三段数据库 delivery、逐页加密 OCR checkpoint、actor rebind、列级 DML/trigger 边界等工程细节与证据矩阵以 [PROJECT_STATUS.md](./PROJECT_STATUS.md) 为唯一权威。
- 证据边界：OCR 与报告内容准确率未评估（`OCR_ACCURACY_NOT_EVALUATED / CONTENT_QUALITY_NOT_HUMAN_REVIEWED`）；macOS runtime 的 RQ `SimpleWorker` 不是 Linux 容器启动证明；残余工程风险（共享 `f1_api` 角色、MinIO orphan、非 latest 历史版本手工处理等）清单见 PROJECT_STATUS。

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

`start` 复用 `scripts/localctl` 的专属 demo 生命周期与 `infra/f1/analysis-reports/migrate.py` 的 `f1_0024` 迁移门，并另外要求 `/api/readyz` 返回精确组件闭集。`A_ECO_ANALYSIS_REPORT_OCR_MODE` 支持 `required`（ARM64 sidecar 硬门）/`disabled`（amd64 无 OCR）/`cloud`（云视觉 OCR，需 `A_ECO_CLOUD_OCR_KEY_FILE` 等，见 DEPLOYMENT.md）。该入口只管理本机专属 demo，不升级或替换远端栈；远程参数化边界见 [deploy/analysis-report/DEPLOYMENT.md](./deploy/analysis-report/DEPLOYMENT.md)。

## 明确未开放

真实客户数据、客户 UAT、生产部署、正式报告签发、法律意见、准确率/Gold 结论、真实生产演练、外部通知和正式小程序发布均未开放。
