# A-Eco Analysis Report Release Candidate Progress

> 本文件按时间记录过程；只有末尾“`neat-freak` 现役事实矩阵”是当前状态。前文出现的“当前/本轮”均是其所在阶段的历史快照，不覆盖末尾结论。

- 目标：把当前分析报告收成服务器可接手部署的本地候选包，不越过人工验收、合并与部署边界。
- 顺序：任务0基线冻结 → PR/迁移审查 → 8路径部署包整合与预检 → 7页双视口视觉收口 → workflow/lint/build/终态审计。
- 2026-08-24 任务0完成：目标仓 clean，HEAD=`1ea4fe3161f94b0397b2016f50571a1b18bf2250`。
- PR #3/#4：均 `OPEN / draft / MERGEABLE`，`statusCheckRollup=[]`；未改 PR。
- lock：两处 SHA256 均为 `8f8f92882ecbcf86d0cd26bbfe91ba35c1c999095cd1600980bb0e0a678c9d4b`。
- 最大风险：无 CI 门禁；迁移默认工程仍锁 0014、专属分析报告链到 0018；远端 DNS/TLS/Keycloak/CORS/PG 参数未知。
- 2026-08-24 任务1完成：`RELEASE_CANDIDATE_REVIEW.md` 冻结 PR #3→#4、改 base 后复核命令与 `f1_0017→f1_0018` 专属迁移边界。
- 风险已显式记录：#3 非 fast-forward、两 PR 均无 CI checks；未执行任何 PR 变更。
- 2026-08-24 任务2导入阶段完成：6份 `deploy/analysis-report/**`、1份 preflight 测试、`TEST_HANDOFF.md` 共8路径与只读来源逐字节一致；SHA/cmp 证据已写仓外。
- 2026-08-24 任务2加固完成：命令单覆盖 DNS/TLS/edge、Keycloak/CORS/VITE、0600 secret/header、PG备份、`0017→0018`、恢复式回滚、双身份与健康度；站点删除需人工二次确认。
- 反向门：HTTP放行、SPA错序、删除`/realms`三组仓外 mutant 均 exit=1；目标树完整 preflight `Ran 19 / OK / skipped=0`，exit=0。
- CLI 渲染只写仓外且产物 `0600`；仓库根 `netlify.toml` 不存在。
- 2026-08-24 任务3 demo：系统 Python 首次启动因缺少既有 `psycopg` 失败；改用工程既有 venv 后成功，未安装依赖。
- demo 状态：`f1_head=f1_0018`、双身份 ready、`generator=deterministic_local`、`ark_calls=0`、`mock_data=0`、`shared_match=1`。
- 视觉截图门连续失败 3 次：`CDP_COMMAND_REJECTED` → `VISUAL_PROVIDER_REPORT_ID_MISSING` → `VISUAL_PUBLISH_BUTTON_MISSING`。
- 只取得客户列表桌面/390px 局部证据；实际覆盖 1/7 页面，14 张门未满足；未改 P1 视觉文件。
- 已按硬规则停止：未作第4次尝试，workflow UAT、lint、build、`git diff --check` 与任务4终态门均未运行。
- demo 已停止；专属 containers/volumes/networks 盘点为 `0/0/0`。
- 强制停止后的只读收尾盘点：11 个变更路径全部在白名单，白名单外 `0`、staged `0`；根级 `netlify.toml`、临时 `node_modules` symlink、`src/web/dist` 均不存在。
- 仓外证据目录/文件权限已统一为 `0700/0600`；凭证形态扫描 `0`、symlink `0`。该盘点不是任务4机器门通过。
- 2026-08-24 新授权侧边浏览器诊断：全新专属 demo 中，以 `tenant-a` 可见 UI 创建报告、确定性生成、提交审核并勾完清单。
- 批准前 `status=review_pending / approve_ready=1`；`POST .../approve` 实测 HTTP 200，随后 `status=approved` 且“发布”可见。
- `POST .../publish` 实测 HTTP 200，随后 `status=published` 且“撤回”可见；控制台 error/warn 为 0。
- 结论收敛：批准/发布后端与页面主流程可用；旧 `VISUAL_PUBLISH_BUTTON_MISSING` 属于仓外自动 runner 的交互/等待不稳定，精确竞态仍未取证。
- 本轮只完成批准→发布侧边浏览器 smoke，没有重跑 14 张门；`VISUAL_CAPTURE_GATE_FAILED_3X` 历史结果不撤销。
- 新 demo 已停止，专属 containers/volumes/networks=`0/0/0`，控制目录不存在；浏览器 secret 值未输出且进程变量已清空。
- 安全例外：登录回调阶段的瞬时浏览器 URL 输出曾包含一次性 OIDC authorization code。该 code 已消费、demo/控制目录已销毁，且未写入仓库或证据文件；仍按“凭证正文零输出”边界记为阻断事实。
- 当时状态（已被末尾新授权轮取代）：`BLOCKED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / TARGETED_TEST_PASSED(task2 only) / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_COMMITTED / NOT_DEPLOYED / NOT_PRODUCTION`。

## 2026-08-24 Imagegen 七页视觉落地轮开工回执

- 目标：把仓外 01→07 单页方向稿落到 7 个真实路由，保持业务、身份、评分与发布边界不变。
- 顺序：冻结既有交付 → 统一视觉基线 → 七页桌面/390px → 侧边浏览器 14 图 → workflow/lint/build/终态审计。
- 任务0相符：HEAD=`1ea4fe3161f94b0397b2016f50571a1b18bf2250`，dirty=`11`，staged=`0`，视觉源码开工前 clean。
- 两份 lock SHA256 均为 `8f8f92882ecbcf86d0cd26bbfe91ba35c1c999095cd1600980bb0e0a678c9d4b`；既有外部 `node_modules` 存在。
- 已在仓外以 `0700/0600` 冻结 TEST_HANDOFF、6 份 deploy 与 preflight 测试共 8 路径指纹。
- 最大风险：方向稿含虚构样例且只覆盖桌面；实现必须沿用真实 API 文案/状态，并单独完成 390px 重排。
- 本轮为旧 3 次 runner 失败后的新授权侧边浏览器轮；旧失败与 OIDC code 暴露历史不得删改。
- 视觉主张：深绿企业壳 + 纸张式报告正文 + 克制状态色；不用卡片瀑布或装饰性渐变。
- 内容顺序：门户先健康度与最新报告，报告页强化阅读，运营台强化客户/材料/审核工作区。
- 交互主张：导航/表格悬停与页面短淡入；遵循 reduced-motion，不新增依赖或装饰动画。
- 七页首轮实现完成：15 个白名单视觉文件有改动；健康度新增测试评分与正式“暂不评分”的显式分栏。
- lint exit=0，保留既有 `react(only-export-components)`、hooks 与 engineering-browser warnings；未冒充零 warning。
- build exit=0：`3228 modules transformed`；保留既有 `>500 kB` chunk warning，未改阈值。
- 侧边浏览器已打开新 demo 登录页；按安全边界不读取或代填 0600 密码，等待人工完成一次登录后继续 14 图门。
- 用户随后显式授权仅为本机合成身份读取并代填密码；首次认证会话超时，重新建立会话后 `tenant-a` 成功落到 `/console/clients`。
- 密码值未回显、未写仓库/证据，临时进程变量与系统剪贴板已清空；侧边浏览器 14 图门继续执行。
- 真实 UI 主流程完成：新建 → 确定性生成 → 提交 → 三项清单 → 批准 → 二次确认发布；客户门户随后可见已发布报告。
- demo 在线重启首次因 migrator `LOCAL_ANALYSIS_REPORT_UAT_COMMAND_FAILED:run:OTHER:1` 失败并自动清到 `0/0/0`；第二次从干净环境启动成功，未达到同门三次失败阈值。
- 14 张最终视口截图齐：7 页各桌面/390px；全部 `scrollWidth <= clientWidth`，方向稿/实现同图对照与 Agent QA 均在仓外。
- P1 修复：门户移动企业栏 `377→375`、报告详情 gutter `377→375`、审核台双列覆盖 `420→375`，均保存 CDP 重放前图与当前后图。
- P1 状态真值修复：已发布/已批准/已替代/已撤回版本刷新后，审核清单只读显示三项完成；最终 `checkedCount=3 / dataCheckedCount=3`。
- 空态与错误态均经真实 UI 观察；测试评分显著标记，正式评分继续 `暂不评分`。
- workflow UAT exit=0、stderr 为空、stdout 仅 canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`；`ark=0 / mock=0 / C/V/N=0 / shared_match=1 / skipped=0`。
- 最终 lint exit=0，保留 19 条既有 warning；build exit=0、`3228 modules transformed`，保留 `>500 kB` chunk warning；`git diff --check` exit=0。
- 最终 PR #3/#4 元数据与 base/head OID 未变，仍无 CI checks；26 个变更路径全部在白名单，staged=0，秘密/客户文字面扫描=0。
- demo、workflow 专属 C/V/N 均为0；临时 `node_modules` symlink 与 `src/web/dist` 已删除；8 路径冻结 SHA 未漂移。
- 本轮：`VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_COMMITTED / NOT_DEPLOYED / NOT_PRODUCTION`。
- 整体仍保留历史 `BLOCKED`：旧 OIDC callback code 输出不可逆；本轮未新增凭证正文输出。

## 2026-08-24 `neat-freak` 现役事实矩阵

- 代码 `verified-current`：目标仍为 `codex/material-report-aeco-polish@1ea4fe3161f94b0397b2016f50571a1b18bf2250`；26 个变更路径全部在原任务白名单，staged=0、白名单外=0。
- 运行态 `pending`：专属 demo containers/running/volumes/networks/control-dir 均为0；`src/web/dist` 与临时 `node_modules` symlink 不存在。先前本地机器证据保留，但当前没有 live 服务，远端仍未授权。
- 文档 `changed-and-verified`：`TEST_HANDOFF / REVIEW / BLOCKED` 已把首轮 runner 失败改为历史事故；用户另行授权后，根 `AGENTS.md / PROJECT_STATUS.md / README.md` 也已同步到当前 PR、迁移与阻断事实。
- 部署命令单 `changed-and-verified(static only)`：发现并移除当前官方 CLI 不支持的 `deploy:list` / `rollback` 形态，改用 `listSiteDeploys / restoreSiteDeploy` API method；`sites:delete` 改为位置参数并保留人工二次确认。未安装 Netlify CLI、未连接 Netlify、未执行回滚或删除。
- 规则 `changed-and-verified`：根规则现已记录默认 `f1_0014`、material-RAG `f1_0016`、analysis-report `f1_0018`、PR #2 已合并及 PR #3 → #4；现役候选入口与无 CI 边界已同源。
- 记忆 `generated-read-only/out-of-scope`：只读用于找历史线索，未获授权写入或直接修改 Codex 生成记忆。
- 工作区 `pending`：4 个 worktree 中 3 个 dirty 且含不等价文件，不能清场；用户在完整汇报后只授权删除 5 个 ignored `__pycache__`，现已精确删除。仓外失败证据/runner/mutant/最终截图仍支撑复核，全部保留。
- 新过程违规：只读文档审计误运行 `command -v netlify || true`；输出为空且无副作用，但触犯硬规则，已追加 BLOCKED。
- 收尾静态门 exit=0：`NEAT_FREAK_STATIC_CHECK_OK status_paths=26 final_images=14 staged=0 outside_whitelist=0`；同时确认旧 Netlify CLI 语法已消失、approve/publish 人工确认门存在、无尾随空白、`dist/node_modules/netlify.toml` 均不存在。
- 清场确认凭证：用户在第一阶段完整汇报后明确回复“授权同步根权威，并只清理 5 个 pycache”；授权未扩展到 worktree、分支、仓外证据或其他 ignored 文件。
- 精确清场 exit=0：删除 `infra/f1/__pycache__`、`infra/f1/analysis-reports/__pycache__`、`scripts/__pycache__`、`src/platform_foundation/__pycache__`、`src/platform_foundation/f1/__pycache__`；输出 `PYCACHE_CLEANUP_OK removed=5`。
- commit 授权凭证：用户在根权威同步与精确清场后明确要求“先提交一下”；授权仅覆盖当前 29 个已审计路径，不包含 push、PR 变更、merge 或 deploy。
- 现役整体：`BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED) / VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。

## 2026-08-31 `f1_0020` 现役真源修正

- 上方 2026-08-24 的固定分数、`f1_0018` 与机器门记录按历史证据保留，不代表当前候选已验收。
- 源码线性 head 现为 `f1_0020`；默认工程仍锁 `f1_0014`，material-RAG 专属目标仍为 `f1_0016`，analysis-report 专属目标为 `f1_0020`。现役远端路径从精确 `f1_0017` 的 pre-0020 备份点线性迁移至 `f1_0020`。
- 现役本地证据生成状态令牌为 `evidence_local`；只有显式前端视觉 mock 保留历史 `deterministic_local`，该令牌不得通过现役 HTTP 合同传输。无可信 scorer 时不新增 health snapshot，HTTP 返回 `snapshot=null`，页面显示“暂不评分”。
- 本轮修正当前只允许记录为 `NOT_TESTED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`；旧 lint/build/browser/UAT 证据不能用来证明新 head、新状态令牌或新迁移目录已通过。历史人工视觉记录保留，但不是本轮工程合同修正的 pending 门。

## 2026-08-31 最终机器真值

- 最终工作树上的离线定向套件已一次连续运行 `Ran 158 / OK`；包含生成恢复、固定服务事项 fixture、多服务商失败关闭与现役部署状态边界。
- 隔离真实 PostgreSQL 集成测试 `26/26` 通过；生成状态门修正后对应状态机用例再跑 `1/1` 通过。
- 前端 lint exit=0、error=0，保留 19 条既有 warning；本轮没有单独执行 npm production build。
- 本地候选 check 通过：`f1_0020 / evidence_local / ready=1 / ark_calls=0 / mock_data=0 / shared_match=1`。
- 自动浏览器工作流通过真实 UI/API/CDP 绑定：报告创建幂等、7 节生成、提交、退回/再生成、批准、发布、客户可见、撤回后隐藏、健康度无伪评分、客户安全服务事项与材料问答均完成；最终 `C/V/N=0 / skipped=0 / ark_calls=0 / mock_data=0 / shared_match=1`。
- 人工视觉验收按本轮要求不属于执行范围，记为 `HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN`，不是 pending 也不是通过。
- 仍未覆盖真实客户数据、真实 Ark/RAGFlow、可信 scorer、远端 smoke、部署与发布验收；QA 的 HTTP 202 同 request ID 重试有合同覆盖，但最终浏览器 fixture 直接返回 200，未强制走 202 分支。
- 当前只支持专属 migrator；直接 Alembic、独立 `roles.sql` 与 downgrade 均为 unsupported boundary。生产前仍需 transactional outbox/sweeper、发起人撤权后的任务终态 reconciler；材料 QA 固定为单服务商 audience，多服务商时 fail-closed；本地抽取按 ID 最多 256 个单位，未证明大语料召回。
- 历史过程阻断不撤销；现役整体：`BLOCKED / TARGETED_TEST_PASSED / SMOKE_PASSED / WORKFLOW_UAT_PASSED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`，不得写作 `RELEASE_VERIFIED`。

## 2026-09-01 `f1_0023` 全自动材料链路收口

- 上方 2026-08-31 的 158/158、PostgreSQL 26/26、lint 和浏览器 UAT 仅作为上一版工作树的历史基线；不能证明本节新增的迁移、worker、OCR 恢复或前端终态。
- Alembic 现役单一 head 升为 `f1_0023`；专属 migrator 目标同步为 0023，默认工程 `f1_0014` 和 material-RAG 专属 `f1_0016` 不变。
- 上传 finalize 与数据库 ingestion delivery 在同一事务注册；dispatcher/worker 只在队列传递 delivery ID 和 token，可恢复队列丢失、过期 lease 和可重试失败。不在队列或 delivery 表存文件正文、OCR 文本、object key 或凭据。
- PDF 逐页 OCR 使用加密 checkpoint，成功后清理，过期项可有界清理；`failed` 或尚有 OCR 债务的 machine-ready analysis 通过不可变 successor 继续，已人工 `confirmed` 但仍有 OCR 债务时固定阻断，不被自动覆盖。
- 分析完成后用第二类数据库 delivery 驱动索引与报告协调；历史 eligible 最新版本有 body-free 幂等回填，无法安全沿用原发起人时写入明确的 actor-rebind 阻断，不伪造身份。
- 报告 job/version 与 generation delivery/outbox 在同一数据库事务注册；数据库 dispatcher 可重投未投递、过期 lease 和可重试任务，RQ 只携带 delivery ID + dispatch token，队列任务身份也包含 token fence。报告状态由数据库单独判定，不再把 Redis/RQ 元数据作为状态真源。
- 报告生成绑定精确 job/version，来源指纹变更会开新版本，旧已发布版保持可见；历史 eligible 最新版本有受限回填，原发起人无效或已撤权时进入显式 actor-rebind 终态，由当前有效管理员经窄数据库能力接管并留审计。worker 禁用、队列投递失败和重试耗尽也有明确终态/恢复原因；report worker 已与 API secret volume 分离。
- `f1_api` 对报告、版本、job、内容、审计、审核证据和健康度快照的表级敏感 DML 已撤销，按现有 API/worker 写入形态改为列级授权；数据库 trigger 固定身份列、状态迁移、当前版本/内容/审核发布门和派生的客户可见性，并约束审核证据、审计与健康度快照写入。该权限边界已经过直接合同、真实增量迁移目录和下述 runtime 恢复验证。
- 正常验证首先暴露并修复了两类假绿：旧 P3 合同仍检查进程内 `BackgroundTasks`，分析报告 migrator 的 FORCE-RLS 闭集漏了 `analysis_report_generation_delivery`。修正后直接合同组合 `Ran 23 tests / OK`；隔离 PostgreSQL 从 `f1_0014→f1_0022→f1_0023` 通过，重放目录验证了 47 表 FORCE-RLS 闭集和关键 definer owner。Pydantic `AutoPipelineOut.schema` 字段命名 warning 已改为内部 `schema_version` + alias `schema`，warnings-as-errors import、序列化和 OpenAPI 对外字段合同通过。专属验证资源已清理为 0，未触碰旧 demo。
- runtime 恢复门使用隔离 PostgreSQL、专属 Redis 7、真实 RQ 队列和生产 `run_generation_job`：Redis unavailable 时 delivery=`retry_wait`，Redis 恢复后 worker 取得数据库 lease；SIGKILL 整个 worker 子进程组，再删除/重建空 Redis 模拟队列元数据全失，数据库过期 lease 产生新 token，stale token 被拒绝，新 worker 从 PostgreSQL 恢复到 `draft/done`（7 节、2 引用）。macOS 普通 RQ Worker fork 触发 Objective-C runtime guard，最终使用独立 OS 子进程中的 RQ `SimpleWorker`；结论覆盖任务/数据库/Redis/token 恢复语义，不覆盖 Linux 容器 `Worker.main` 启动证明。
- OCR checkpoint 恢复门使用真实隔离 PostgreSQL：页 1/3 checkpoint 以 AES-GCM 加密落库且无 OCR 明文，新进程只重试缺失页 2 并完成 `ready`、3 页、OCR debt=0、成功后 checkpoint=0；另重新插入并合法过期 1 条，由 SECDEF purge 删除且残留 0。OCR adapter 为 synthetic，只证明 checkpoint/恢复/加密/清理，不证明 OCR 准确率。
- 当前浏览器 UAT `./scripts/localctl analysis-report-workflow-uat-check` exit=0、耗时 111.933 秒、stderr 空并输出 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`；报告 7 节/2 引用、审核、发布/撤回、客户列表/详情/服务摘要/材料问答及 health null 均通过，专属 C/V/N=0、共享 demo 指纹不变。该流程预置 eligible fixture 材料，不覆盖浏览器上传→OCR checkpoint→索引→报告全链。
- Chrome/Tencent TAT 只读预检确认服务器为 `x86_64/amd64`、2 vCPU/4 GiB、Ubuntu 24.04、Docker 29、Compose 2.40；当时内存约 2.6 GiB 已用/1.0 GiB 可用、空闲 swap 约 1.4 GiB、磁盘可用约 47 GiB，未绑定 SSH key。服务器已有 Compose project `anhuan-ar-demo-0c74bf2a11ee`：10 个容器 healthy 约 20 小时，web=`127.0.0.1:58103`，health/ready 正常，数据库 head=`f1_0020`。但该栈缺少 0021/0022/0023、关键文件哈希与当前工作树不同且没有 OCR container，只能标为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。当前已增加 `A_ECO_ANALYSIS_REPORT_OCR_MODE=disabled` 与最终 Compose override，amd64 无 OCR config check 通过，仓外无秘密源码包已生成；它尚未上传/启动，只覆盖 native-text PDF。当前 `f1_0023` 工作树仍为 `NOT_DEPLOYED`。
- 已知剩余边界：API 与 report worker 仍共享 `f1_api` 数据库角色，列级授权和 trigger 不能替代后续专用角色/更窄 capability；数据库可约束健康度快照的状态与归属，但不能独立重算或鉴别评分内容。ingestion worker 仍使用 `f1_api` 和 MinIO root 凭据；MinIO 对象写入成功但 DB finalize 失败可产生孤儿对象；分析与本地索引可能重复 OCR；自动历史回填只覆盖 eligible 的最新版本，非 latest 历史版本仍是手工处理/历史兼容边界；Redis 长期故障时报告 delivery 会持续重试，而 job status 尚未投影 delivery attempt/reason，运维可观测性待补。当前 runtime 门还不覆盖 RQ scheduler 延迟重试、长期 Redis outage、ingestion/material-pipeline worker 完整 Linux runtime。
- 当前结论：`TARGETED_TEST_PASSED / LOCAL_WORKFLOW_UAT_PASSED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`；这里的 `NOT_DEPLOYED` 精确指当前 `f1_0023` 工作树。服务器历史 `f1_0020` demo 为 `REMOTE_OLD_DEMO_RUNNING / NOT_CURRENT_CANDIDATE / NOT_PRODUCTION`。历史过程阻断继续保留；不得写作 `RELEASE_VERIFIED`、当前候选远端 E2E 通过或生产就绪。

## 2026-09-01 `f1_0023` 无 OCR 候选远端部署与 native-text 冒烟（2026-09-02 补记）

- 上一节「尚未上传/启动」按当时时刻保留；本节补记同日晚间经用户明确授权执行的远端动作：确认上传源码包，并 stop 旧 demo（不 `down`、不删卷）。
- 仓外 2 MiB 无秘密源码包上传测试服务器（`x86_64/amd64`、2 vCPU/4 GiB、Ubuntu 24.04、Docker 29、Compose 2.40，预检同上节），以新版本目录/新 project 启动 `f1_0023` 无 OCR 栈。
- 旧 demo `anhuan-ar-demo-0c74bf2a11ee`（`f1_0020`）：10 个容器全部 stopped、保留；14 个卷、2 个网络未删除。它是回滚点，不是独立备份；回滚演练（新栈停→旧栈起→readiness/数据核对）尚未执行。
- 新栈 11/11 容器 healthy、全部 `OOMKilled=false`；web 仅监听服务器 loopback，经 SSH 隧道以 `http://127.0.0.1:44087/` 访问，无公网 DNS/TLS 入口。
- 远端 native-text PDF 定向冒烟通过：上传 `202` → ClamAV `clean` → 原生文本解析 `ocr_required=false` → 预览/Redis 调度/本地索引 `ready` → 自动生成第 1 版报告草稿（7 节，引用含本次 PDF）；未提交/审批/发布，客户端已发布列表前后不变。
- 服务器回执：`/home/ubuntu/aeco-test/smoke-evidence/native-pdf-smoke-c81e611634c64655bdad33d35c10223c.json`；测试留下 `ZZ-SYNTH-AUTO-*` 合成资料及对应草稿作为证据，业务级清理 API 仍缺（不得在线手删数据库与对象存储）。
- 未覆盖并保持原边界：远端浏览器 E2E `NOT_TESTED_POLICY_BLOCKED`（浏览器自动化组件禁止操作 localhost，未绕过安全策略）；服务器故障注入（Redis 重启/丢队列、worker SIGKILL、主机重启）与回滚演练未实跑；扫描件 OCR 不可用（no-OCR 候选，`OCR_REQUIRED` fail-closed）；多页/大文件/并发/中文 CID 字体未测；无 systemd 自启、监控告警与正式备份恢复；`HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN`。
- 安全：服务器密码曾在对话中明文出现，且服务器未绑定 SSH key；建议尽快轮换密码并绑定 SSH key。
- 治理：部署物来自未提交工作树的仓外包，服务器有 SHA 回执，但不能对应到可审计、可复现的 Git 提交（当前 HEAD 仍为 `955a274`，其上有 119 个未提交路径：89 已跟踪修改 + 30 未跟踪）。
- 当前结论：`TARGETED_TEST_PASSED / REMOTE_NATIVE_TEXT_SMOKE_PASSED / TEST_SERVER_DEPLOYED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / REMOTE_BROWSER_E2E=NOT_TESTED_POLICY_BLOCKED / NOT_COMMITTED / NOT_PUSHED / NOT_PRODUCTION`；`TEST_SERVER_DEPLOYED` 精确指 loopback+SSH 隧道的测试服务器部署，不含公网入口。历史过程阻断继续保留；不得写作 `RELEASE_VERIFIED`、扫描 PDF 全链路可用或生产就绪。

## 2026-09-02 `f1_0024` 云端 OCR 适配器（api key 模式）+ GLM-5.3-Flash live 冒烟通过

- 新增 `src/platform_foundation/f1/features/material_intake/cloud_ocr.py`：显式 opt-in 的云视觉 OCR；provider 闭集 `glm_vision`（智谱，默认 `open.bigmodel.cn/api/paas/v4`）/`ark_vision`（火山方舟）；`F1_MATERIAL_CLOUD_OCR_DIALECT=chat|anthropic` 选择请求方言（anthropic 方言走 `/api/anthropic` 兼容端点，可使用 GLM Coding Plan 额度，默认端点同步切换）。API key 仅从 0600 常规文件读取（`F1_MATERIAL_CLOUD_OCR_API_KEY_FILE`），模型 id 必须显式给（`F1_MATERIAL_CLOUD_OCR_MODEL`）；仅 https、禁重定向、禁代理、certifi 根证书的校验式 TLS（永不关闭验证）；页图仅取 DCTDecode/JPEG 内嵌图（零新解码依赖），其余滤镜 fail-closed `OCR_UNAVAILABLE`；传输层可注入，离线测试零外呼；请求缓冲用后清零。
- 引擎选择 `resolve_ocr_engine()`：本地 FIFO 启用时恒为 FIFO（`f0h-ppocrv6-3.9.2`）；仅 FIFO 关闭且云端显式配置时用云引擎（厂商中立 backend `cloud-vision-chat-1`）；能力不 ready 时逐页 `OCR_UNAVAILABLE`/`OCR_DISABLED`，`OCR_REQUIRED` 语义不变。
- checkpoint 身份泛化：`OCR_PARSER_BACKENDS` 闭集（FIFO + cloud-vision-chat-1）；`ocr_checkpoint_aad`、`_checkpoint_body`、`load_ocr_checkpoints(parser_backend=…)`、`persist_ocr_checkpoint`（按 result.backend 绑定 AAD）、`analyze_pdf(ocr_pages=…, ocr_parser_backend=…，未知 backend 拒绝)`、p3 processor 单引擎接线；跨 backend checkpoint 依旧拒绝并清理。
- `f1_0024` 迁移：放宽 `material_ocr_checkpoint_parser_backend_check` 为两值闭集；downgrade 先删云 backend 行再收窄。`migrate_f1` 闭集/analysis-report 目标/各 head 断言（migrate.py、uat、demo、browser fixture、authz/postgres 集成、P2/P3/engineering-closeout 静态）同步到 0024，并修复四项既有陈旧断言（P2/P3 head=0015、非法目标 f1_0021、portal GET 路由闭集）。
- 离线验证：`tests/test_material_cloud_ocr.py` 22 项 OK（配置 fail-closed、密钥模式、glm/anthropic 端点与方言请求形态、base64 页图、短文本、传输失败不泄漏密钥、非 DCT、页上限、身份失配、AAD 闭集、checkpoint body、analyzer 接受与跨 backend 拒绝、未知引擎拒绝）；离线回归 7 套件 `Ran 113`，112 绿 + 1 环境性失败（本 checkout 无 `src/web/node_modules`，`P5_TYPESCRIPT_COMPILER_MISSING`，按规则未安装依赖）。
- **live 冒烟（用户授权，真实外呼）**：本机 Chrome 无头渲染中文安环文本页 → JPEG → 嵌入极简 PDF（1024×768 DCTDecode）；`glm_vision + anthropic 方言 + glm-5.3-flash`，密钥取自用户 CC Switch（写入仓外 `~/.anhuan-cloud-ocr/glm_api_key`，0600，未经 stdout 打印——注意该 key 曾在配置结构探测时被误打印一次于会话记录，建议轮换）。结果：3.5s、`applied / OCR_APPLIED`、116 字符、4/4 关键词命中（双重预防/危险化学品/环氧丙烷/重大危险源）、`cloud-vision-chat-1` 身份与 64-hex unit_id 完整。曾先用 `paas/v4` 端点被 429/1113（开放平台余额不足）拒绝，改 anthropic 方言走 Coding Plan 额度后通过；本机 venv 缺系统 CA 根证书导致 TLS 秒败，已用锁内 certifi 修复。
- 边界：单页合成中文印刷体、一次调用；不证明多页/表格/手写/低质扫描件准确率、并发与成本上限；OCR 质量与准召未评估（`OCR_ACCURACY_NOT_EVALUATED`）；compose/远端启用 wiring 未做。密钥不进仓库、不进 compose、不进提交。
- 现役整体新增标签：`CLOUD_OCR_OFFLINE_CONTRACT_PASSED / CLOUD_OCR_LIVE_SMOKE_PASSED(glm-5.3-flash) / OCR_ACCURACY_NOT_EVALUATED / NOT_PUSHED / NOT_PRODUCTION`（本轮按用户授权完成本地 commit）。
