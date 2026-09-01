# 本机分析报告专属测试环境交接

本目录只描述 **本机可重复启停** 的专属测试栈。
2026-08-24 的组件级机器门证据保留为历史记录，不覆盖当前“无可信 scorer 则不评分”合同。
当前状态：`TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`；其中 `NOT_DEPLOYED` 精确指当前 `f1_0023` 工作树。服务器上的历史 `f1_0020` demo 为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。
整体证据仍保留 `BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED)`；不得写 `RELEASE_CANDIDATE_LOCAL_PASSED`。
不是共享 `anhuan-f1` 栈，不是远端预发，不是生产。

## 2026-09-01 当前工作树边界

下方 2026-08-31 的浏览器、lint 和候选启停证据只代表上一版未提交工作树；本节新增证据才代表当前 `f1_0023` 工作树。上传/分析/报告 delivery、OCR checkpoint、actor rebind、DB-only status、敏感写入列级 DML/trigger 及前端重试语义现已取得定向合同、真实恢复 runtime 与当前浏览器 workflow UAT，状态为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`。

### 2026-09-01 正常验证证据

- Docker 隔离、无网络的直接合同组合：`Ran 23 tests / OK`，覆盖 47 表迁移目录、上传 durable registration 顺序、报告 outbox/token/lease、自动 pipeline replay、并发版本锁和 worker 失败终态。
- 真实专属 PostgreSQL 先从空库迁移到 `f1_0014`，再到 `f1_0022`，最后精确执行 `f1_0022→f1_0023`；输出 `LOCAL_ANALYSIS_REPORT_MIGRATE_OK`。
- 目录重放通过：47 张预期表全部纳入 migrator 闭集；`material_pipeline_delivery`、`material_ingestion_delivery`、`material_ocr_checkpoint`、`analysis_report_generation_delivery` 均为 `RLS=true / FORCE=true`，关键 SECDEF owner 与属主表一致。
- Pydantic `AutoPipelineOut.schema` 命名 warning 已通过内部 `schema_version` + alias `schema` 修复；warnings-as-errors import 通过，序列化/OpenAPI 仍输出 `schema`。
- 真实 runtime 恢复门：隔离 PostgreSQL + 专属 Redis 7 + 真实 RQ 队列 + 生产 `run_generation_job` 下，先验证 Redis unavailable→`retry_wait`，再验证 worker SIGKILL、Redis 删除/重建后队列元数据全失、过期 lease 新 token、stale token 拒绝及新 worker 从 PostgreSQL 恢复至 `draft/done`（7 节、2 引用）。macOS 使用独立 OS 子进程中的 RQ `SimpleWorker`，证明恢复语义但不证明 Linux 容器 `Worker.main` 启动链。
- 真实 PostgreSQL OCR checkpoint 恢复门：AES-GCM 加密保存页 1/3 且库中无 OCR 明文，新进程只请求缺失页 2，最终 `ready`、3 页、OCR debt=0、checkpoint=0；另验证合法过期项由 SECDEF purge 删除 1、残留 0。OCR adapter 为 synthetic，不是 OCR 准确率门。
- 当前浏览器 workflow UAT exit=0、耗时 111.933 秒、stderr 空并输出 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`；7 节/2 引用、审核、发布/撤回、client list/detail/services/QA 与 health null 均通过，专属 C/V/N=0、共享 demo 指纹未变。材料由 fixture 预置为 eligible，不证明浏览器上传→OCR checkpoint→索引→报告全链。
- 验证专属容器、三个卷与网络已精确删除，残留为 0；原 `f1_0020` demo 的 11 个容器保持 healthy，未变更。

## 2026-08-31 上一版工作树证据

- 最终工作树上的离线/合同定向闭集已一次连续运行 `Ran 158 / OK`；包含生成恢复、固定服务事项 fixture、多服务商失败关闭与现役部署状态边界。
- 真实隔离 PostgreSQL：分析报告/RLS/owner/grant 主门 `Ran 26 / OK`；重复生成状态机修正后精确运行 1/1 通过。
- 前端 lint exit=0，保留 19 条既有 warning；本轮未单独执行 `npm run build`。`git diff --check` exit=0。
- repo-relative candidate `start` / `check` 通过：`f1_head=f1_0020 / generator=evidence_local / ready=1 / ark_calls=0 / mock_data=0 / shared_match=1`。
- 最终扩展浏览器门 exit=0：报告创建、异步生成、7 节/2 报告引用、提交、三项审核证据、批准、发布、客户列表/详情、client-safe 服务摘要、材料 QA/2 引用、无评分空态、撤回隐藏全部通过；`ark_calls=0 / mock_data=0 / skipped=0`。
- 收尾后专属 containers/volumes/networks/control directory 均为 0；`src/web/node_modules`、`src/web/dist`、根 `netlify.toml` 均不存在。未触碰用户既有 notebook/surrealdb 容器。

## 2026-08-24 历史交卷结果

- 部署 preflight 定向门：`Ran 19 / OK / skipped=0`，`TARGETED_TEST_PASSED`。
- 独立新授权侧边浏览器轮已取得 7 页 × 桌面/390px 共 14 张最终截图，全部 `overflowX=false`；三个响应式 P1 与一个审核清单状态真值 P1 已有前后证据。
- workflow UAT exit=0、stderr 为空，canonical 输出满足 `ark_calls=0 / mock_data=0 / dedicated_c=0 / dedicated_v=0 / dedicated_n=0 / shared_match=1 / skipped=0`。
- lint exit=0，保留 19 条既有 warning；build exit=0、`3228 modules transformed`，保留 `>500 kB` chunk warning；`git diff --check` exit=0。
- demo 已停止，专属 containers/volumes/networks 为 `0/0/0`；临时 `src/web/node_modules` symlink 与 `src/web/dist` 均不存在。
- PR #3/#4 元数据未变，仍为 `OPEN / draft / MERGEABLE / statusCheckRollup=[]`；这是无 CI 门禁风险，不是 CI 通过。
- 首轮 runner 的三次失败仍在 `RELEASE_CANDIDATE_BLOCKED.md` 作为历史事故保留，不再表示当前 14 图缺失。整体证据仍因历史 OIDC callback code 输出与本次收尾过程命令违规保持 `BLOCKED`。

上述 2026-08-24 workflow 证据使用过时的固定分数预期，只能证明当时的实现。2026-08-31 已按新合同重跑 PostgreSQL 与浏览器：发布后报告可阅读，无可信 scorer 时不写入健康度快照，HTTP 返回 `snapshot=null`，页面显示“暂不评分”。

## 启停

在仓库根目录：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan

./scripts/localctl analysis-report-demo-start
./scripts/localctl analysis-report-demo-status
./scripts/localctl analysis-report-demo-stop
```

`start` 只打印三行，不含密码：

```
url=http://127.0.0.1:<port>
provider_username=tenant-a
client_username=invitee
```

`status` 在就绪时为闭集 JSON，键仅为：

`ready, f1_head, provider_login_ready, client_login_ready, workflow_seeded, generator, ark_calls, mock_data, shared_match`

其中 `f1_head` 必须精确为 `f1_0023`；默认工程仍是 `f1_0014`，只有分析报告专属 migrator 到 0023。

停止后专属容器/卷/网络为 0，控制目录删除。失败也必须收口，禁止按前缀扫共享栈。

## 两个角色

| 用户名 | 用途 |
| --- | --- |
| `tenant-a` | 服务商运营台：创建报告、生成、提交、批准、发布、撤回 |
| `invitee` | 客户门户：仅已发布版本可见；撤回后列表空、详情「内容不存在」 |

密码在专属控制目录的 0600 secret 文件中，本交接文件不写出口令。
`employee` 保持企业 A / `plant_admin`，fixture 不改其 membership/角色。

## 前端 mock 与后端合成材料（必须分开）

- **前端 mock 关闭**：`VITE_MATERIAL_RAG_REPORT_MOCK` 未启用；页面不得出现「本地合成数据」。浏览器走真实 UI 与 HTTP。
- **后端本地证据生成器**：`F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1`、`F1_LOCAL_ENGINEERING=1` 且 `F1_EXTERNAL_PIPELINES_ENABLED=false`。fixture 写入的是 **released + clean + preview-ready 的合成材料**，仅供 JOIN 生成 7 节、引用≥2 的草稿。这不是真实客户数据，不是真实模型/Ark 生成，也不提供正式评分依据。
- `status.generator=evidence_local` 只标识报告生成路径；`ark_calls=0`，`mock_data=0`。它不表示已有健康度 scorer。
- 无可信 scorer 时，发布成功但数据库不新增 health snapshot；`GET /api/v1/analysis-reports/health/latest` 返回 `snapshot=null`，首页和健康度详情页都显示“暂不评分”。

## 能力边界

当前 `f1_0023` 已在本机浏览器重复验证：报告创建 → 异步生成首个版本 → 提交审核 → 勾选清单 → 批准 → 发布 → 客户阅读/下载 HTML → 客户服务摘要与带引用材料问答 → 撤回后对客户不可见。材料为预置 eligible fixture，因此它是当前报告/客户主流程证据，不是上传/OCR/索引的浏览器全链证据。

不可声称：真实 Ark/RAGFlow 生成通过、远端预发授权、生产就绪、人工验收已完成。

浏览器全流程门：`./scripts/localctl analysis-report-workflow-uat-check`
成功时 stdout 仅 canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`，stderr 空。
当前关键摘要应包含 `health_null_after_publish=1 / health_snapshot_count=0 / health_null_after_withdraw=1 / client_services=1 / client_qa=1 / qa_citation_count>=1`。

当前结论仅允许：

当前自动化差异记录为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`，不得记录 `RELEASE_VERIFIED` 或 `RELEASE_CANDIDATE_LOCAL_PASSED`。人工视觉明确为 `NOT_PART_OF_THIS_RUN`，不是本轮 pending/必需门。

当前工作树已用数据库 delivery + dispatcher/worker 取代开关启用时的进程内回调；上传/分析/报告 generation 均可从数据库重投，报告 job/version 与 outbox 同事务注册，RQ 身份包含 dispatch-token fence，状态只以数据库为准。逐页 OCR checkpoint 可恢复，失败 analysis 以新修订继续；历史 eligible 最新版本可受限回填，失效或撤权 actor 进入显式 rebind。报告/version/job/content/audit/review-evidence/health-snapshot 的表级敏感 DML 已改为列级授权，并增加状态、发布、客户可见性、证据与审计 trigger。以上已取得直接合同+增量迁移目录+真实 Redis/worker/OCR 恢复的 `TARGETED_TEST_PASSED`，以及 fixture 边界内的 `LOCAL_WORKFLOW_UAT_PASSED`。

仍未闭合的工程边界：API 与 report worker 仍共享 `f1_api` 角色；数据库不能独立重算或鉴别健康评分内容；ingestion worker 仍使用 `f1_api` + MinIO root；对象写入成功而 DB finalize 失败可留下 MinIO orphan；分析与本地索引可能重复 OCR；多个 active provider/client audience 仍失败关闭；本地抽取仍有 unit 上限；自动回填只覆盖 latest eligible version，非 latest 历史版本仍需手工处理；Redis 长期故障时报告 delivery 会持续重试，job status 尚未投影 delivery attempt/reason。runtime 还未覆盖 RQ scheduler 延迟重试、长期 Redis outage、ingestion/material-pipeline worker 完整 Linux runtime；当前 OCR 镜像仅 ARM64，而服务器为 amd64。

## 远端交付包（与本机 demo 分开）

上方启停命令只用于本机 `localctl analysis-report-demo-*`。
本段描述仓库内 `deploy/analysis-report/`：**基线已有本地提交，当前修正未提交、未 push；当前 `f1_0023` 工作树尚未部署。服务器确有历史 `f1_0020` demo，不能把它表述成当前候选部署。**

- PR 栈：PR #3 先合；PR #4 改 base 到 `main` 后复核只剩本层；本轮不改 PR、不合并。
- 拓扑：Netlify 静态前端 + 单一 HTTPS edge；`/api`、`/realms`、`/resources` 同源 rewrite，SPA fallback 最后。
- 渲染：`python3 deploy/analysis-report/preflight.py --netlify-origin <HTTPS> --edge-origin <HTTPS> --output <仓外路径>/netlify.toml`
- 操作说明：`DEPLOYMENT.md` / `ROLLBACK.md` / `REMOTE_SMOKE.md`
- 数据库：先建 pre-0023 PG 备份点，再用 `infra/f1/analysis-reports/migrate.py` 线性前向 `f1_0017→f1_0023`；回退只能恢复备份到新数据库，禁止 downgrade。
- 身份与网络：Keycloak issuer/redirect/web origin、CORS、DNS、TLS、edge 路由与双身份 smoke 均在部署命令单中闭合；Bearer 只进 0600 header/config 文件，不进 curl argv。
- **前端 mock 关闭** 与 **后端合成 fixture** 仍然分开：不得设 `VITE_MATERIAL_RAG_REPORT_MOCK=1`；本地证据生成器若在测试 edge 打开，只服务于测试材料，不是真实客户数据，也不是 Ark。
- 本机浏览器 UAT 通过 **不是** 远端部署证据。
- Chrome/Tencent TAT 只读预检：`x86_64/amd64`、2 vCPU/4 GiB、Ubuntu 24.04、Docker 29、Compose 2.40；当时内存约 2.6 GiB 已用/1.0 GiB 可用、空闲 swap 约 1.4 GiB、磁盘可用约 47 GiB，且未绑定 SSH key。
- 远端已有 Compose project `anhuan-ar-demo-0c74bf2a11ee`：10 个容器 healthy 约 20 小时，web=`127.0.0.1:58103`，health/ready 正常，数据库 head=`f1_0020`。它缺 0021/0022/0023，当前工作树关键文件哈希均不同，且没有 OCR container；结论只能是 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。
- 升级到当前 `f1_0023` 需要先备份历史数据库/数据、安排停机并生成兼容 amd64 的新交付包；本轮没有执行备份、停机、迁移、替换或 remote smoke，当前工作树保持 `NOT_DEPLOYED`。

远端结论仅允许：

当前自动化差异为 `TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED`。amd64 无 OCR/native-text PDF 候选模式和仓外无秘密源码包已经准备完成，但上传源码、短暂停止旧容器、启动新 project 与 remote smoke 都尚未执行；完整 amd64 OCR/扫描 PDF 仍需独立 runtime 锁。当前工作树为 `BLOCKED / TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`；远端历史栈为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。

本地当前机器门不覆盖远端；`TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION` 保持，直到完成提交授权、远端备份/停机、amd64 运行包、SSH key、安全升级与 remote smoke。旧 `f1_0020` demo 的在线状态不改变当前候选的 `NOT_DEPLOYED`。
