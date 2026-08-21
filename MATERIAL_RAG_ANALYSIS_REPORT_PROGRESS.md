# MATERIAL RAG Analysis Report Progress

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
