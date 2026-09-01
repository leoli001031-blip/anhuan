# A-Eco 分析报告发布候选审查

> 2026-09-01 现役覆盖：本文下方 `f1_0018`、视觉 pending 与已提交状态是 2026-08-24 的冻结取证，不再是当前真源。现役单一 head 为 `f1_0023`，当前自动化差异为限定范围 `TARGETED_TEST_PASSED / HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`；以 [PROJECT_STATUS.md](./PROJECT_STATUS.md) 与 [RELEASE_CANDIDATE_PROGRESS.md](./RELEASE_CANDIDATE_PROGRESS.md) 末尾为准。

## 1. 冻结结论

- 合并顺序固定为：PR #3 先合入 `main`，PR #4 后合。
- PR #3 合入后，必须把 PR #4 的 base 从 `codex/material-report-integration` 改为 `main`，再复核 PR #4 只剩当前视觉/健康度层。
- 用户已另行授权本地 commit；仍不执行 push、改 PR、mark ready、merge 或 deploy。
- 当前组件级机器门已经通过，但整体候选因历史 OIDC callback code 输出和本次收尾过程命令违规保持 `BLOCKED`，不得晋升为 `RELEASE_CANDIDATE_LOCAL_PASSED`。人工、远端与发布边界仍是 `COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。

## 2. 当前 PR 证据（2026-08-24）

| PR | base | head | 状态 | mergeable | checks |
| --- | --- | --- | --- | --- | --- |
| #3 | `main@dd466e5a39867b996abce60272b93e798e9b1d81` | `codex/material-report-integration@6cdbba30b6c4a278a9d3de89f0d243c201291d52` | `OPEN / draft` | `MERGEABLE` | `statusCheckRollup=[]` |
| #4 | `codex/material-report-integration@6cdbba30b6c4a278a9d3de89f0d243c201291d52` | `codex/material-report-aeco-polish@1ea4fe3161f94b0397b2016f50571a1b18bf2250` | `OPEN / draft` | `MERGEABLE` | `statusCheckRollup=[]` |

发布风险：两个 PR 都没有 CI status checks。`statusCheckRollup=[]` 表示“没有 CI 门禁”，不得解释成“CI 通过”。PR #3 与当前 main 的共同基线是 `af0d74470a81275a64be08638d7272197bd53095`，不是 fast-forward；合并前仍须依赖 GitHub 的实时冲突检查和授权后的门禁复核。

## 3. 层级与差异冻结

- PR #3 层：`1882ad0 → 5885b8a → 6cdbba3`；当前相对 `main@dd466e5` 为 83 files / 13,486 insertions / 468 deletions。
- PR #4 当前层：`6cdbba3 → 1ea4fe3`，只有一个提交；冻结参考为 72 files / 5,402 insertions / 343 deletions。
- `6cdbba3` 是 `1ea4fe3` 的祖先；该关系已用 `git merge-base --is-ancestor` 验证，exit=0。

授权合并 PR #3 后，服务器/PR 执行者必须运行：

```bash
git fetch origin --prune
gh pr edit 4 --base main
gh pr view 4 --json state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,mergedAt,mergeable,statusCheckRollup
git diff --name-status origin/main...origin/codex/material-report-aeco-polish
git diff --shortstat origin/main...origin/codex/material-report-aeco-polish
```

复核要求：`baseRefName=main`；PR #4 仍指向授权时确认的 head；三点差异不得重新带回 PR #3 已合内容，业务层应与冻结的 `6cdbba3..1ea4fe3` 对应。任何额外文件、冲突、head 漂移或未知 CI 结果都停止 PR #4 合并并重新审查。

## 4. 迁移顺序与隔离边界

- 线性 Alembic 顺序：`f1_0016 → f1_0017 → f1_0018`。
- `f1_0017_analysis_reports.py`：`revision=f1_0017`，`down_revision=f1_0016`。
- `f1_0018_analysis_report_health_snapshot.py`：`revision=f1_0018`，`down_revision=f1_0017`；增加不可变健康度快照。
- 默认工程迁移目标仍是 `F1_DEFAULT_MIGRATE_TARGET=f1_0014`。
- material-RAG 专属目标仍是 `f1_0016`。
- 只有 `infra/f1/analysis-reports/migrate.py` 请求 `F1_ANALYSIS_REPORT_MIGRATE_TARGET=f1_0018`，并核验单一 F1 head 精确为 `f1_0018`。
- 不得把默认 seed/verify/backup 或默认工程目标升级到 0018；远端分析报告部署只走专属 migrator 的前向 `0017 → 0018` 路径。

## 5. 视觉与用户主流程审查（现役）

- 独立新授权侧边浏览器轮已完成 7 个页面各桌面与 390px，共 14 张最终截图；最终页面均为 `overflowX=false`。
- 真实 UI 已走通新建、确定性生成、提交、三项审核、批准、人工二次确认发布，以及 `invitee` 客户侧阅读。
- 已修复三个响应式 P1 和一个审核清单状态真值 P1；问题、文件、前后截图与红→绿数值见第 7 节。
- `deterministic_local` 明示为测试能力；正式评分继续显示“暂不评分”。空态、错误态、导航与关键操作均已观察。
- 首轮仓外自动 runner 的三次失败保留为历史事故证据，不再表示当前缺图或当前发布按钮不可用；精确 runner 竞态仍未取证，但不阻断已完成的侧边浏览器现役证据。
- Agent 检查不替代甲方签字，视觉终态仍为 `HUMAN_VISUAL_ACCEPTANCE_PENDING`。

## 6. 当前审查状态

- 组件级机器门：`VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL`。
- 整体证据：`BLOCKED(HISTORICAL_OIDC_CALLBACK_CODE_OUTPUT / FORBIDDEN_OR_TRUE_COMMAND_USED)`；不得写 `RELEASE_CANDIDATE_LOCAL_PASSED`。
- 发布边界：`PR_STACK_REVIEWED_LOCAL / MIGRATION_ORDER_REVIEWED_LOCAL / NO_CI_CHECKS_RELEASE_RISK / COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。
- `VISUAL_CAPTURE_GATE_FAILED_3X` 与 `SIDE_BROWSER_TRANSITION_SMOKE_PASSED` 仅为早期 runner 事故链，不再是现役视觉状态。

## 7. Imagegen 七页视觉落地复核（新授权轮）

本节是旧三次 runner 失败后的独立侧边浏览器授权轮；首次 runner 事故与安全例外仍保留在 `RELEASE_CANDIDATE_BLOCKED.md`。仓外证据根为：

`/Users/lichenhao/Desktop/安环项目/artifacts/aeco-release-candidate-closeout-20260824-v1/visual-implementation-v1`

### 7.1 最终 14 图矩阵

| 页面 | 桌面截图 | 390px 截图 | 最终 overflow |
| --- | --- | --- | --- |
| `/portal` | `01-portal-desktop.png` | `01-portal-mobile-390.png` | false / false |
| 报告列表 | `02-report-list-desktop.png` | `02-report-list-mobile-390.png` | false / false |
| 报告详情 | `03-report-detail-desktop.png` | `03-report-detail-mobile-390.png` | false / false |
| 健康度 | `04-health-desktop.png` | `04-health-mobile-390.png` | false / false |
| 客户列表 | `05-clients-desktop.png` | `05-clients-mobile-390.png` | false / false |
| 客户材料 | `06-client-materials-desktop.png` | `06-client-materials-mobile-390.png` | false / false |
| 报告审核台 | `07-report-workbench-desktop.png` | `07-report-workbench-mobile-390.png` | false / false |

14 张均来自用户指定的 Codex 侧边浏览器，使用本机合成身份 `tenant-a` / `invitee`；截图为稳定视口，不使用出现内容重复的 full-page stitching。七份方向稿与实现同图对照见 `qa-01-desktop-comparison.png` → `qa-07-desktop-comparison.png`，详细 Agent 复核见仓外 `design-qa.md`。该复核不替代甲方签字。

### 7.2 P1 问题 → 文件 → 前后截图

| 问题 | 修复文件 | 前 | 后 |
| --- | --- | --- | --- |
| 门户移动企业栏 20px 负边距与新 18px 页头不一致，`377 > 375` | `src/web/src/index.css` | `p1-before-01-portal-mobile-replay.png` | `01-portal-mobile-390.png` |
| 报告详情移动端保留 Ant Row 40px gutter，`377 > 375` | `src/web/src/index.css` | `p1-before-03-report-detail-mobile-replay.png` | `03-report-detail-mobile-390.png` |
| 审核台后置双列规则覆盖 `<1280` 单列规则，`420 > 375` | `src/web/src/index.css` | `p1-before-07-workbench-mobile-replay.png` | `07-report-workbench-mobile-390.png` |
| 已发布版本刷新后审核清单看似未勾选，和状态真值冲突 | `src/web/src/pages/console/ReportWorkbenchPage.tsx`、`src/web/src/index.css` | `p1-before-07-checklist-desktop-comparison.png` | `07-report-workbench-desktop.png` |

前三张“前”图由侧边浏览器 CDP 重放当时已经实测变红的同一 CSS 约束，仅用于保留红→绿证据；仓库当前代码与 14 张最终截图均为修复后状态。数值原文见 `visual-overflow-red-green.txt`。审核清单最终 DOM/样式为 `checkedCount=3 / dataCheckedCount=3 / background=rgb(47,125,97) / checkmark=rgb(247,251,248)`。

### 7.3 主流程、状态与机器门

- 真实 UI 流程：新建 → `deterministic_local` 生成 → 提交审核 → 三项清单 → 批准 → 人工二次确认发布；发布后客户门户可见。
- 明示边界：客户页显示“测试环境·确定性评分”，正式环境显示“暂不评分”；没有隐藏真实失败，也没有把 60 分当正式结论。
- 空态：真实观察“暂无报告，点击右上角新建”。错误态：真实观察“内容不存在 / 该内容不存在、未发布或已被撤回 / 重试”，390px 无溢出。
- workflow UAT exit=0、stderr 为空；stdout 只有 canonical JSON 与 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`。其中 `ark_calls=0 / mock_data=0 / dedicated_c=0 / dedicated_v=0 / dedicated_n=0 / shared_match=1 / skipped=0`。
- 最终 lint exit=0，保留 19 条既有 warning；最终 build exit=0，`3228 modules transformed`，保留 `>500 kB` chunk warning；`git diff --check` exit=0、stdout 为空。
- PR #3/#4 最终仍 `OPEN / draft / MERGEABLE / statusCheckRollup=[]`，base/head OID 未变；这仍是“无 CI 门禁”的发布风险，不是 CI 通过。

### 7.4 新授权轮结论

- 组件级机器门：`VISUAL_IMPLEMENTATION_LOCAL_PASSED / WORKFLOW_UAT_PASSED / TARGETED_TEST_PASSED / DEPLOYMENT_PACKAGE_INTEGRATED_LOCAL`。
- 整体发布候选仍为 `BLOCKED`：此前一次瞬时 OIDC callback code 已进入对话输出；本次 `neat-freak` 只读审计又误用了任务书硬禁的 `|| true`。两项都无法事后改写为未发生，因此不得晋升为 `RELEASE_CANDIDATE_LOCAL_PASSED`。
- 其余边界：`COMMITTED_LOCAL / NOT_PUSHED / HUMAN_VISUAL_ACCEPTANCE_PENDING / REMOTE_TARGET_PENDING / NOT_DEPLOYED / NOT_PRODUCTION`。
