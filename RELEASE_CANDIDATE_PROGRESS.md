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
