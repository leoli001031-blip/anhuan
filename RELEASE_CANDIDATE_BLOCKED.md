# A-Eco Analysis Report Release Candidate Blocked

## 现役整体阻断

- 组件级机器门已经通过：14 张最终截图、workflow UAT、部署 preflight、lint、build 与 diff-check 均有证据。
- 整体证据仍保持 `BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED)`，不得晋升为 `RELEASE_CANDIDATE_LOCAL_PASSED`。
- 第一项：一次历史浏览器状态输出包含已消费的一次性 OIDC authorization code，无法事后满足任务书的严格零输出条件。
- 第二项：本次 `neat-freak` 只读文档审计误运行 `command -v netlify || true`；exit=0、stdout/stderr 为空、没有写入或远端副作用，但任务书硬禁 `|| true`，同样不能改写为未发生。
- 当前没有已知 P1 页面或批准/发布主流程阻断；本地 commit 已获授权收口，人工视觉签字、push、PR 变更/合并、远端目标与部署仍未发生。

## 历史视觉门失败：`VISUAL_CAPTURE_GATE_FAILED_3X`（非现役视觉阻断）

首次任务3的仓外自动 runner 连续失败 3 次，当时按任务书停止该门及后续任务4，没有第 4 次尝试。之后另获授权的侧边浏览器轮已经独立完成 14 张现役截图和全部本地机器门；下列内容只保留首次失败的事故证据。

原始失败输出：

```text
attempt 1 exit=1
LOCAL_BROWSER_VERIFY_FAILED CDP_COMMAND_REJECTED

attempt 2 exit=1
LOCAL_BROWSER_VERIFY_FAILED VISUAL_PROVIDER_REPORT_ID_MISSING

attempt 3 exit=1
LOCAL_BROWSER_VERIFY_FAILED VISUAL_PUBLISH_BUTTON_MISSING
```

- attempt 1：CDP 截图命令被拒；未产出 PNG。
- attempt 2：fixture 只有材料，未得到报告 ID；在失败前只产出客户列表桌面/390px 两张。
- attempt 3：仓外 runner 以本地合成身份执行创建、生成、提交、批准后，页面未出现预期发布按钮；在失败前仍只产出客户列表桌面/390px 两张。
- 14 张要求未满足。两次尝试的客户列表截图是重复页面，实际只覆盖 7 页中的 1 页；其余页面未取证。
- 已检查 attempt 3 的客户列表局部截图：桌面与 390px 未见横向拖动、逐字换行或操作遮挡；该局部结论不得外推到其他页面。
- 未修改任何 P1 视觉文件；没有可列的“问题 → 文件 → 前后截图”。
- `./scripts/localctl analysis-report-workflow-uat-check`、lint、build、`git diff --check`、最终 PR 元数据复核均因强制停止条件而未运行。
- 仅执行了安全收尾所需的只读路径盘点：白名单外 `0`、staged `0`；这不构成任务4通过。

### 新授权侧边浏览器诊断（2026-08-24）

新开干净 demo 后，侧边浏览器真实 UI 已走通“新建 → 生成 → 提交 → 勾选清单 → 批准 → 发布”：

```text
before_status=review_pending
approve_ready=1
POST /api/v1/analysis-reports/versions/{version_id}/approve -> 200
after_approve_status=approved
publish_visible=true
POST /api/v1/analysis-reports/versions/{version_id}/publish -> 200
after_publish_status=published
withdraw_visible=true
console_error_count=0
```

因此当前不是后端批准/发布能力阻断，也不是页面缺少发布能力。历史第三次失败收敛为仓外自动截图 runner 的交互/等待不稳定；当时未保存 `/approve` 状态码及 DOM 状态，无法再诚实细分为点击丢失或刷新竞态。

该 smoke 在当时没有重跑完整 14 张门，因此不能改写首次 `VISUAL_CAPTURE_GATE_FAILED_3X`。后续独立新授权轮已经通过侧边浏览器完成 14 张现役取证；若未来继续维护旧 runner，仍建议补 `/approve` 响应绑定、状态快照与语义点击，但这不是当前用户主流程阻断。

### 安全例外：瞬时 OIDC 回调 code 输出

侧边浏览器登录完成时，一次浏览器状态输出包含了回调 URL 及一次性 OIDC authorization code。没有输出 `tenant-a` 密码，也没有把 code 写入仓库、截图或仓外证据文件。该 code 已被本次登录消费；随后 demo 停止、专属容器/卷/网络清零、控制目录删除，因此不可在已销毁环境复用。

尽管风险已收口，这仍不满足任务书“禁止读取、打印或提交 token/身份证明”的严格零输出要求，必须作为阻断事实保留，不得写成秘密/身份证明输出为零。

### 收尾过程例外：硬禁命令守卫被使用

2026-08-24 的 `neat-freak` 只读文档审计中，子任务误运行：

```text
command -v netlify || true
```

命令 exit=0，stdout/stderr 为空；未安装依赖、未修改文件、未登录 Netlify、未调用远端 API。它仍直接违反任务书“禁止 `|| true`”的硬规则，因此作为过程阻断保留。

## 知识治理解除记录：`ROOT_AUTHORITY_SYNCHRONIZED`

- Codex 实际规则链为用户级 `~/.codex/AGENTS.md` → 仓根 `AGENTS.md`。
- 用户在第一阶段完整汇报后明确授权同步根权威。`AGENTS.md`、`PROJECT_STATUS.md` 与 `README.md` 已更新到默认 `f1_0014`、material-RAG `f1_0016`、analysis-report `f1_0018` 及 PR #3 → #4 的当前事实。
- `PROJECT_STATUS.md` 同时修正 PR #2：其现役状态是 2026-08-21 已合并，不再是 OPEN+draft。
- 下次接手先读 `PROJECT_STATUS.md`；进入本候选后再依次读 `RELEASE_CANDIDATE_PROGRESS.md / BLOCKED.md / REVIEW.md`。两项整体过程阻断没有因知识同步而解除。

## 启动与清理边界

首次 demo 启动使用系统 Python，exit=1，原始错误尾行为：

```text
ModuleNotFoundError: No module named 'psycopg'
```

改用仓库既有 venv（未安装依赖）后启动成功：

```text
url=http://127.0.0.1:51217
provider_username=tenant-a
client_username=invitee
```

就绪状态原文：

```json
{"ark_calls":0,"client_login_ready":1,"f1_head":"f1_0018","generator":"deterministic_local","mock_data":0,"provider_login_ready":1,"ready":1,"shared_match":1,"workflow_seeded":1}
```

停止命令 exit=0，stdout/stderr 均为空；随后只读盘点：

```text
dedicated_c=0
dedicated_v=0
dedicated_n=0
```

首次 runner 阶段交卷状态（历史）：

`BLOCKED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / TARGETED_TEST_PASSED(task2 only) / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_COMMITTED / NOT_DEPLOYED / NOT_PRODUCTION`

## 历史待解：`SIDE_BROWSER_MANUAL_LOGIN_PENDING`（已解除）

- 本节记录等待人工登录时的历史快照；解除事实见下方“解除记录”。当时新授权视觉轮的专属 demo 已就绪，状态为 `f1_0018 / deterministic_local / ark_calls=0 / mock_data=0 / shared_match=1`。
- 侧边浏览器已停在本机 Keycloak 登录页；任务边界禁止读取或输出 0600 password，浏览器安全边界也禁止 Agent 代填敏感凭证。
- 未读取、打印或填写密码/token/回调 URL；等待人工在已打开的侧边浏览器完成一次登录后继续 7 页 × 双视口取证。
- 不受影响项已继续：七页首轮视觉代码完成，lint/build 均 exit=0；14 图、overflow 红→绿、workflow UAT、diff-check 和终态清理仍未完成。
- 这不是本轮截图门的失败次数；未伪造截图，也未回退到旧仓外 runner。

### 解除记录（2026-08-24）

- 用户在动作发生前显式授权 Agent 为本机专属 demo 的合成身份读取并代填密码。
- 首个 Keycloak 会话已超时；Agent 从应用登录入口重新建立认证会话，`tenant-a` 随后成功进入 `/console/clients`。
- 密码正文未进入对话、仓库、截图或证据文件；临时变量与系统剪贴板已清空。
- `SIDE_BROWSER_MANUAL_LOGIN_PENDING` 已解除；历史 `VISUAL_CAPTURE_GATE_FAILED_3X` 与 OIDC code 安全例外仍原样保留。

## 新授权侧边浏览器轮收口（2026-08-24）

- 新授权轮已独立取得 7 页 × 桌面/390px 共 14 张最终截图；全部最终页面 `overflowX=false`。
- 报告流程经真实 UI 完成创建、生成、提交、三项审核、批准、二次确认发布，随后由 `invitee` 在门户读取。
- 三个响应式 P1 与一个审核状态真值 P1 已修复，前后截图、红→绿数值和设计对照均保存在仓外 `visual-implementation-v1`。
- workflow UAT exit=0、stderr 为空，canonical 输出满足 `ark_calls=0 / mock_data=0 / dedicated_c=0 / dedicated_v=0 / dedicated_n=0 / shared_match=1 / skipped=0`。
- 最终 lint/build/diff-check 均 exit=0；lint 的 19 条既有 warning 与 build 的大 chunk warning 原样保留。

### 非终态失败：在线 demo 重启一次失败

为了让静态 web 镜像载入第一处响应式修复，首次在 demo 仍在线时调用 `analysis-report-demo-start`；web image build 完成后，migrator 返回：

```text
LOCAL_ANALYSIS_REPORT_UAT_COMMAND_FAILED:run:OTHER:1
```

异常处理已自动停止并清理该专属环境，盘点为 `containers=0 / volumes=0 / networks=0 / control_dir_exists=0`。第二次从干净环境启动成功；之后只用同项目的 `docker compose build web` 与 `up --no-deps --force-recreate web` 更新静态前端，数据库卷未主动删除。该启动门只有一次失败，没有触发连续三次停止规则。

### 仍然存在的整体发布阻断

视觉落地轮没有新增密码、token、客户资料或凭证正文输出；26 个仓库变更路径全部在白名单，staged=0。但历史“瞬时 OIDC callback code 输出”和本次收尾过程命令违规都已经发生，无法事后满足任务书的严格过程条件，因此整体发布候选不能诚实改写成无阻断。

当前整体状态：

`BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED) / VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL / COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`
