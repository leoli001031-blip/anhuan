# A_ECO_MVP_RECOVERY_PROGRESS

## 第四轮正常验证（2026-08-23）

- [x] 真实双身份登录与路由边界：`tenant-a` 进入运营台，`invitee` 进入客户门户；客户直达 `/console/clients` 与 `/workbench` 均回到 `/portal`。
- [x] 服务商报告闭环：对同一客户新建报告、生成第 1 版、核对 7 节与 2 条引用、提交审核、三项审核勾选、批准、发布、撤回均经真实浏览器完成；生成器仍为本地确定性实现，Ark 调用为 0。
- [x] 客户发布边界：发布后 390px 门户可读报告与引用，页面无横向溢出，正文不显示 UUID、tenant、dataset、chunk、SHA、lease、request id 或 Ark；撤回后首页立即变为「暂无已发布报告」，旧详情 URL 的 API 返回 404，页面显示「内容不存在、未发布或已被撤回」。
- [x] 材料与入口实页核查：客户名称主链接进入总览，材料/报告保留快捷入口；材料桌面与 390px 安全列表显示「入库处理完成/待确认」等真实生命周期文案，上传入口仍由 capability 控制。
- [x] 正常验证中发现并最小修复两个真实接线问题：
  - 客户主页误请求 P2 workbench overview（客户返回 `executor` 视图）导致 fail-closed 错误；主页已改为只消费 client-safe published-report 合同。
  - 客户服务事项缺 client-safe/audience 合同；门户导航已隐藏该入口，`/portal/services` 直达回首页，不再暴露不完整功能。
- [x] 最终静态复验：复用既有依赖、未 install；`npm --prefix src/web run lint` exit=0（仅既有 warning）；`npm --prefix src/web run build` exit=0，3225 modules，Vite built in 216ms；临时 `node_modules` symlink 与 `dist` 已删除。
- [ ] 两个历史自动化 runner 尚未跟随新 IA 更新：dual-identity runner 仍等待旧 `/portal/qa`，workflow runner 仍只从桌面 `<tr>` 找报告入口；本轮未改 runner，人工浏览器结果不能写成自动化 runner 通过。

## 第四轮收尾状态

`FRONTEND_LINT_BUILD_PASSED / MANUAL_DUAL_IDENTITY_BROWSER_UAT_PASSED / MANUAL_REPORT_WORKFLOW_BROWSER_UAT_PASSED / CLIENT_PUBLISHED_WITHDRAWAL_BOUNDARY_PASSED / AUTOMATED_BROWSER_RUNNER_UPDATE_PENDING / SERVICE_ITEMS_CONTRACT_BLOCKED / MATERIAL_UPLOAD_BROWSER_MUTATION_NOT_RUN / NOT_COMMITTED / NOT_PRODUCTION`

## 第三轮进度

- [x] 任务 0 封存现场（31=21M+10??，D/R=0，staged=0，diff-check=0，SHA 已记录）
- [x] 任务 1 材料生命周期收真：
  - 1.1 补回 `setMaterialIntakeClassification` 导入（TS 阻断）
  - 1.2 删除资格假绿：阶段改「入库处理完成」，删除资格列与推导函数；blocked 等归「待确认」并显示 reason_code 文案；汇总条同步改名
  - 1.3 上传按钮由 `getIngestionCapabilities` 驱动：upload_enabled=false 或 scanner 非 ready → 隐藏按钮并显示原因；版本动作仍只消费 allowed_actions
  - 1.4 上传幂等键在一次未知结果期间保持不变，仅成功/明确冲突/重开流程时更新
  - 1.5 活动状态（等待处理/安全检查/文字识别与解析）5s 静默刷新；每次响应复核 scope_kind+client_account_id，混入即 409 fail-closed
  - 1.6 390px 改安全字段列表（useNarrow），不再依赖七列横拖
- [x] 任务 2 报告身份与迟到响应：工作台先 `listClientReports(clientId)` 证明归属，未命中 404；绑定前禁止读版本/动作/轮询；adapter 校验 published-detail/version/job 响应 ID（不符抛 RESPONSE_ID_MISMATCH）；detail 用 epoch 拒绝迟到响应；generate request_id 随 client/report 切换重置；动作按 session.capabilities（generate/review/publish/withdraw）缺失即隐藏
- [x] 任务 3 入口/错误/服务事项：客户名主入口进总览（材料/报告为快捷动作）；门户首页报告失败显式错误+重试、overview 非 enterprise 视图 fail-closed；Login 显示 OIDC authError + 重试；未知服务状态→「状态待确认」；运营台移除服务事项 tab+路由+孤立页面，门户服务事项只留列表；MockBadge 注释明确 HTTP fixture 无可靠标志不宣称
- [x] 任务 4 一次验证（实际输出）：
  ```
  test ! -e src/web/node_modules → OK；ln -s 复用既有 node_modules（未 install）
  tsc -p src/web/tsconfig.app.json --pretty false → TSC_EXIT=0（一次通过）
  git diff --check → DIFFCHECK_EXIT=0
  ! rg '可用于报告|报告资格' MaterialPanel.tsx → RG_GATE=PASS（无命中）
  trap 已删 symlink → SYMLINK_REMOVED ✓
  ```
  白名单外 SHA 零漂移（fixture ed5c5d69…/generator 280b9618… 与开工一致）；staged=0；未 commit/push。

## 第三轮收尾状态

`FRONTEND_MVP_RECOVERY_PARTIAL / MATERIAL_LIFECYCLE_UI_HARDENED / REPORT_CLIENT_BINDING_IMPLEMENTED / SERVICE_ITEMS_CONTRACT_BLOCKED / TYPECHECK_PASSED / BROWSER_UAT_PENDING / NOT_COMMITTED / NOT_PRODUCTION`

## 第三轮开工回执（2026-08-23，任务 0 封存现场）

- 基线：branch=codex/material-report-aeco-polish ✓，HEAD=6cdbba3… ✓，dirty=31（21M+10??）✓，D/R=0 ✓，staged=0 ✓，diff-check=0 ✓。
- 白名单外 dirty SHA（逐字节保留）：fixture ed5c5d69…0bee；generator 280b9618…9acb。
- **降级声明**：上一轮 `FRONTEND_MVP_RECOVERY_IMPLEMENTED` 降级为 `FRONTEND_MVP_RECOVERY_PARTIAL`——其中「报告资格前端推导」「客户服务事项 tab」「门户服务详情抽屉」经复核不符合“可见状态来自真实合同”，本轮收回修正。
- 目标：状态全来自真实合同；客户/报告身份闭合；缺合同能力诚实隐藏；材料不再假报资格。
- 顺序：任务1 材料收真 → 任务2 报告身份与迟到响应 → 任务3 入口/错误/服务事项 → 任务4 记录+一次 tsc 验证。
- 最大风险：本轮只跑一次 tsc（无 build），类型错误必须靠人工自查前置消除。

## 第二轮存档

（以下为上轮记录，保留）

## 开工回执（任务 0，基线核对全部通过）

- 基线：branch=codex/material-report-aeco-polish ✓，HEAD=6cdbba3… ✓，dirty=14（11M+3??）✓，staged=0 ✓，diff-check=0 ✓，node_modules 不存在 ✓。
- 白名单外 dirty（逐字节保留）：`infra/f1/analysis-reports/local_browser_fixture.py` sha256=ed5c5d69…0bee；`src/platform_foundation/f1/features/analysis_reports/generator.py` sha256=280b9618…9acb。
- 目标：把演示切片恢复成最小环保托管业务闭环——材料可信入库（P3 复用）、客户总览、统一服务事项（P2 复用）、报告交付，客户门户首页化。
- 顺序：任务1 主链 P1（门户落地/会话失败可恢复/状态清理/生成上下文保留/新建客户隐藏）→ 任务2 材料 MVP → 任务3 客户总览 → 任务4 服务事项 → 任务5 门户首页 → lint+diff-check。
- 最大风险：P2 服务事项按“客户”维度无后端过滤（租户=企业而非 CRM 客户）——运营台客户内服务事项宁可诚实“未接入”，不跨域展示；客户门户用本企业租户调用，属正确域。

## 进度

- [x] 任务 0 基线核对与回执
- [x] 任务 1 主链 P1：客户落地 /portal（SessionAccess.homePathFor + LegacyProviderGate）；membership 空/失败→可恢复错误+重试（adapters/index.tsx membershipError 链路，NO_MEMBERSHIP）；路由变化清旧状态（useClient/工作台 reportId 重置）；生成任务 sessionStorage 持久化+断查重试+版本归属回读校验；正式 HTTP 隐藏「新建客户」（仅演示环境）；MockBadge 改「测试环境 · 演示数据」
- [x] 任务 2 材料入库 MVP：MaterialPanel 重写——汇总条（总数/处理中/待确认/失败/可用于报告）、业务状态映射七态、报告资格列（仅由可见信号推导，索引信号未暴露前端、最终以后端生成为准，已在此声明）、详情抽屉（安全预览 page_text/image、版本历史、失败原因 reasonCopy、process/retry/release/reject 只消费 allowed_actions、分类确认）、上传成功只报「文件已接收，正在处理」；mock 分支保留简表
- [x] 任务 3 客户总览：`/console/clients/:clientId` 新页（服务阶段/下次跟进/资料状态汇总/最新报告），ClientShell 收成 总览｜资料｜服务事项｜报告 四 tab；缺 API 的字段未造假（见 BLOCKED-1）
- [x] 任务 4 统一服务事项：门户 `/portal/services` 复用 P2（列表：类型/统一状态/负责人有无/期限/逾期；抽屉：描述、问题与整改要求、处理记录、时间线无操作人 ID；动作仅按 allowed_actions 渲染，client 无新增写权限）；状态文案统一 待处理→处理中→待确认→已完成（ServiceItemsShared）；运营台客户内服务事项诚实未接入（BLOCKED-1）；未恢复五个旧菜单
- [x] 任务 5 门户首页：`/portal` 即首页（最新报告/下次服务/待办含逾期标记/资料状态说明），导航收成 首页｜服务事项｜分析报告，QA 仅演示环境；mock 下 P2 无合成数据→真实空态
- [x] 验证（lint + diff --check）
  - 临时 symlink 复用 node_modules（未 install），`npm --prefix src/web run lint` → **exit=0，0 error**（唯一 src 警告为既有 p3/SpreadsheetPreview exhaustive-deps，非本轮文件）；symlink 已删
  - `git diff --check` → exit=0
  - 白名单外 SHA 零漂移：fixture ed5c5d69…0bee / generator 280b9618…9acb，与开工记录一致；staged=0；未 commit/push
  - 未运行 build/E2E/Docker（任务书限定）

## 收尾状态

`FRONTEND_MVP_RECOVERY_IMPLEMENTED / LINT_PASSED / BUILD_NOT_RUN / BROWSER_UAT_PENDING / NOT_COMMITTED / NOT_PRODUCTION`
