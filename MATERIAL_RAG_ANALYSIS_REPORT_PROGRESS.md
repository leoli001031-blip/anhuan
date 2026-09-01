# MATERIAL RAG Analysis Report Progress

## 2026-08-22｜workflow browser UAT + local demo handoff｜PASSED

- 目标：独立分支上完成工作流浏览器 UAT 与本机 demo 启停交接；checkpoint 已在 `5885b8a`，本提交为第二枚普通 commit。
- 工作流 live：r1 `ANALYSIS_REPORT_INTERNAL_LABEL_LEAK`（泄密正则误伤 `tenant-a@fixture.invalid`）；r2–r4 `APPROVE_BUTTON_DISABLED`（antd 两字按钮插空格前误判、受控 Checkbox 未写入 React 状态）；r5–r8 仍卡在批准匹配；r9 `OIDC_REDIRECT_STALLED`（登录后不能直达深层 workbench）；r10/r12 `WITHDRAWN_DETAIL_NOT_DENIED`（api.ts 与 adapters `ApiError` 不是同一 class，404 被当成 unknown）；r11 `PUBLISH_WITHDRAW_MISSING`（发布确认后等待撤回，属模态点击时序）。r13 exit=0，stdout canonical JSON + `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`，stderr 空，wall=86.19s，`ark_calls=0`，专属 C/V/N=0，`shared_match=1`。
- Demo rehearsal 1/1：start→status→min_check→stop，wall=61.31s，`generator=deterministic_local`，`ark_calls=0`，`mock_data=0`，控制目录已删，leftover demo=0。
- 离线合同 38/OK；fixture PG 此前 fixture-v1 1/OK 未重跑（本轮未改其覆盖语义后的身份门）。lint=0、build=0；用既有 `node_modules` symlink，lock SHA=`8f8f92882ecbcf86d0cd26bbfe91ba35c1c999095cd1600980bb0e0a678c9d4b`，用后删除 symlink 与 `src/web/dist`。
- 前端 mock 关闭与后端合成 fixture 分开：页面无「本地合成数据」；生成器为本地确定性路径，不是 Ark。
- 仓外新证据包 `artifacts/material-rag-analysis-report-workflow-browser-uat-20260822-v1` 根=`2c7dabfa810ca44ac0ee0e49996a4598c64563eb6ad1f2b62a08375bbdb673ce`；未覆盖 dual-identity 根=`be6e6f0bb29c38b07c27e4f6979754111e84e4420866d421724c9dfaec13693e`；contract-v2 根=`d5549c861b41d9a24f9f55a9907fa4ac4e7f46178f9131a95a60a1b9e776eba3`。
- 未 mark ready、未 merge、未改 main、非 force push。`REMOTE_STAGING_TARGET_NOT_AUTHORIZED`。`HUMAN_ACCEPTANCE_PENDING`。`NOT_PRODUCTION`。


## 2026-08-22｜frontend tenant hardening + dual-identity UAT｜PASSED

- 目标：封前端租户串线与 fixture 身份冲突，并取得真实双身份浏览器证据；报告全流程另记 PENDING。
- 顺序：行为门 → tenantFetch/六旁路/invitee → 真实 fixture PG → unittest → lint/build → `analysis-report-uat-check` → 合同 `<br>` 与仓外证据。
- 最大风险：登录后一帧 `tenantReady` 仍为登出值，session 未就绪被旧门送回 `/login`；UAT 冒充既有 stage；共享栈漂移。
- 建议替换（已落地，未改禁止文件）：① compose `seed` 锁死 head=`f1_0014`，UAT 改为 host 调 `local_seed._ensure_*` 且断言 `f1_0017`；② overlay `localnet.internal: false`，否则宿主机 `127.0.0.1:LOCAL_PG_PORT` 被挡；③ `loginToPath` 只认本树「登录」，不改 `Login.tsx`、不扩 `BROWSER_STAGES`；④ Docker 发布端口后 `inet_server_addr()` 是容器 IP，fixture loopback 门只留在 `pg_host()`。
- 不 commit/push。旧 evidence-v2 根未覆盖。`git diff HEAD^ HEAD --check` 仍是历史 commit 红灯，未伪称已修。

## 2026-08-22｜dual-identity UAT + tenantFetch｜STARTED

- 目标：封租户串线与 fixture 身份冲突，并拿到真实双身份浏览器证据；报告全流程另记 PENDING。
- 顺序：行为红灯 → tenantFetch/六旁路/invitee fixture → 真实 PG 门 → lint/build → analysis-report-uat-check → 合同 `<br>` 与证据。
- 最大风险：localStorage 仍参与 no-op、六旁路漏接、UAT 冒充 material-rag、共享栈漂移。
- 不 commit/push。UAT 最多 2 个 live 周期。

## 2026-08-22｜tenant snapshot + fixture identity guard｜PASSED

- 唯一目标检查（只跑一次）：
  `PYTHONPATH="$PWD/src:$PWD" F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan /Users/lichenhao/Desktop/安环项目/.venv/bin/python3 -B -m unittest tests.test_engineering_closeout_frontend_api`
  输出：`Ran 3 tests in 0.017s` / `OK` / `real 0.06` / exit=0；failures/errors/skipped=0。
- 租户：membership 显式 `enterpriseId: null`；binder 不读 localStorage；报告请求未就绪则 `TENANT_SNAPSHOT_UNREADY` 且不 fetch；setter 与 native `storage` 同走 `invalidateTenantContext`（generation++ → abort → 清快照）；stale `then/catch/finally` 用 `born !== getTenantGeneration()` 丢弃。
- fixture：写前只读 preflight（双 flag、loopback、pgint 闭集+后缀、current_database/user、head=`f1_0017`、control dir/receipt）；源码无 membership DELETE / role 覆盖；异常固定码 + rollback。pgint 只补 `non_sensitive_identity_env` + `identity.receipt`，不启动栈。
- 复核：HEAD=`1882ad0106618525c206622c290aab5648e9bb47` staged=0；`git diff --check`=0；`git diff HEAD^ HEAD --check` 仍只报冻结合同 Markdown 行尾空格（exit=2）；App/LegacyProviderGate/合同 SHA 与 evidence-v2 root=`8317e37bc124763ffae93d4d946b3d491ea60c4686bdbdb6a3f8638b933a7b2b` 未变；白名单外 OUTSIDE_WHITELIST=NONE；未 commit/push。
- 现役：`TARGETED_TEST_PASSED / FRONTEND_TENANT_CONTEXT_HARDENING_IMPLEMENTED / LOCAL_FIXTURE_TARGET_IDENTITY_IMPLEMENTED / CHECKPOINT_DIFF_CHECK_WAIVER_PENDING / FRONTEND_BUILD_REVALIDATION_PENDING / FIXTURE_RUNTIME_NOT_TESTED / BROWSER_UAT_PENDING / NOT_PUSHED / NOT_PRODUCTION`

## 2026-08-22｜tenant snapshot + fixture identity guard｜STARTED

- 目标：封企业切换串上下文与 fixture 误写库两个 P1；只跑一次 <60s 前端合同。
- 顺序：核基线 → 内存 tenant snapshot → fixture 写前身份门 → 只改既有第三项 → 唯一 unittest。
- 最大风险：binder 仍读 localStorage、stale then/catch 落 B 状态、fixture 在 preflight 前 DML。
- 不跑 build/PG/Docker/浏览器；不 commit。现役将含 `CHECKPOINT_DIFF_CHECK_WAIVER_PENDING`。

## 2026-08-22｜checkpoint + frontend UAT wiring｜STARTED

- 目标：先把当前 61 路径封成可恢复本地 commit，再补旧路由隔离、租户会话原子性与 A→B fixture，使前端达可进浏览器 UAT 的代码状态。
- 顺序：A 只改两份状态文档并 checkpoint → B 白名单接线/fixture → py_compile/lint/build 各一次。
- 最大风险：git add 越界、59 路径漂、把 UI gate 当后端边界、或把 fixture 写成 Keycloak 已验收。
- 核：branch/HEAD/61=8M+53??/NUL SHA/记录根/v1 仅 PROGRESS+BLOCKED 漂，均相符。不跑 Docker/PG/浏览器。

## 2026-08-22｜analysis report integration checkpoint｜SEALED

- 保留 `REPORT_AUTHORIZATION_RUNTIME_PASSED / REPORT_RLS_NON_RECURSIVE_PASSED / CLIENT_AUDIENCE_BINDING_RUNTIME_PASSED`。
- `FRONTEND_UNCHANGED` 精确改为 `FRONTEND_UNCHANGED_SINCE_INTEGRATION_SEAL`。本提交不等于前端 build、浏览器 UAT 或 production。
- 仓外 evidence-v2 复制 v1 原始 19 项并封存最终 61 文件；旧 raw 重锚，未重跑测试。未 push。
- 建议替换：`git diff --cached --check` 因冻结合同 `MATERIAL_RAG_ANALYSIS_REPORT_API_CONTRACT.md` 的 Markdown 硬换行（行尾两空格）为 2；不改 59 路径字节，优先保全 61 文件可恢复。
- 现役：`ANALYSIS_REPORT_CHECKPOINT_COMMITTED / CHECKPOINT_EVIDENCE_CLOSED / FRONTEND_UNCHANGED_SINCE_INTEGRATION_SEAL / NOT_PUSHED / NOT_PRODUCTION`

## 2026-08-22｜frontend route gate + local fixture｜PASSED

- LegacyProviderGate 包住旧 `/` 树：仅 provider_admin 渲染 Layout；client_user 转 `/portal/qa`；加载/错误不先出 Layout。login/callback/portal/console 未包裹。
- 企业切换中止在途请求、丢弃旧 session 并重取；请求冻结同一企业快照；session.enterprise_id 与请求头不一致 fail-closed。UI 门不是后端安全边界。
- 专属 fixture 仅 `F1_LOCAL_ENGINEERING=1` 且 head=`f1_0017`：复用 tenant-a / employee，A enterprise_admin 单 membership、B plant_admin 单 membership、A 属 CRM 与 A→B active binding。未跑 fixture 运行时，不是 Keycloak 验收。
- 验证各一次且首次 exit=0：py_compile / lint / build。已删本任务 node_modules symlink 与 dist。staged=0。
- 现役：`FRONTEND_ROUTE_GATE_IMPLEMENTED / LOCAL_REPORT_FIXTURE_IMPLEMENTED / FRONTEND_LINT_BUILD_PASSED / FIXTURE_RUNTIME_NOT_TESTED / BROWSER_UAT_PENDING / NOT_PRODUCTION`

## 2026-08-22｜authz RLS closeout + PG runtime｜PASSED

- 静态合同先红后绿：client 自引用、report↔version 环、migrator 漏 audience/非 41 表、三类关系 FK 均先捕获；修复后 16/OK。
- 真实 PG 门 3 周期：① audience UNIQUE 双绑失败 ② 把绑定客户企业误当他租户 ③ 15 项 exit=0 skipped=0 wall=8.28s。专属 C/V/N=0，anhuan-f1 身份前后相同。
- 无 DEFINER/BYPASSRLS/USING(true)；binding 仍 fixture；前端与旧 seal `src/web/**` 字节一致；HEAD 仍 `af0d744`，staged=0，未 commit。
- 证据：`artifacts/material-rag-analysis-report-authz-rls-runtime-20260822-v1`（未覆盖旧 seal）。本机 `python3` 无 psycopg，PG 门用安环 `.venv/bin/python3`。
- 现役：`REPORT_AUTHORIZATION_RUNTIME_PASSED / REPORT_RLS_NON_RECURSIVE_PASSED / CLIENT_AUDIENCE_BINDING_RUNTIME_PASSED / BACKEND_CHECKPOINT_READY / FRONTEND_UNCHANGED / NOT_COMMITTED / NOT_PRODUCTION`

## 2026-08-22｜authz RLS closeout + PG runtime｜STARTED

- 目标：无环 fail-closed RLS、41 表 FORCE RLS、关系 FK、真实 PostgreSQL ≥12；最多 3 个专属 Docker 周期。
- 基线：`codex/material-report-integration` @ `af0d744`；56 路径、D=0/staged=0/`git diff --check=0`；48 非 authz + `src/web/**` 与 seal 字节一致。
- 顺序：先锁静态红灯合同 → 最小修 f1_0017/migrator/service → 合同绿 → 专属 PG 门 → 仓外新证据包。
- 硬边界：不新建 0018、无 DEFINER/BYPASSRLS/USING(true)、binding 仅 fixture、不动前端/默认 compose、不 commit。
- 现役：`REPORT_AUTHORIZATION_RUNTIME_PENDING / FRONTEND_UNCHANGED / NOT_COMMITTED / NOT_PRODUCTION`

## 2026-08-21｜authorization hardening｜PASSED

- `product_role_for` 只认当前 membership `tenant.role`；分析报告域无 `tenant.roles` 补权。provider 各表 RLS 均要求当前 sub 在该 enterprise 有 `super_admin|enterprise_admin`。
- `f1_0017` 新增 `analysis_report_client_audience`（不建 0018）；`f1_api` 只读、无自动 UUID 插入。client SQL/RLS 经 active binding，已删除 `client_account_id == session enterprise`。
- 白名单外 48 路径 hash 未漂；默认 migrate 仍 `f1_0014`；冻结合同 16/OK。`python` 不在 PATH（127）；同内容 `python3 -B` 一次 `PYTHON_AST_OK 5` exit=0。未 commit。建议：本机用 python3 代替 python。
- 现役：`REPORT_AUTHORIZATION_HARDENING_IMPLEMENTED / CLIENT_AUDIENCE_BINDING_IMPLEMENTED / SECURITY_RUNTIME_VALIDATION_PENDING / FRONTEND_UNCHANGED / NOT_COMMITTED / NOT_PRODUCTION`

## 2026-08-21｜authorization hardening｜STARTED

- 目标：修跨 membership 角色提升与 UUID 相等冒充客户；service+RLS 同时收紧；不跑 Docker/迁移/测试，只一次 AST。
- 基线：`codex/material-report-integration` @ `af0d744`；7M+49??=56、D=0/staged=0/`git diff --check=0`；两源 manifest mismatch=0；合同16/OK。封存 `artifacts/material-rag-analysis-report-authz-hardening-seal-20260821-v1`。
- 顺序：仅 `tenant.role` 定 provider → f1_0017 加 audience binding 与 RLS → AST 一次。
- 最大风险：只改 service 不改 RLS、自动按 UUID 插 binding、扩大 GRANT。建议：无。
