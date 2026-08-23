# A_ECO 健康度快照进度

## 2026-08-24｜真实浏览器重验与 checkpoint 收口

- 工作流门新增健康度闭集：客户经真实 HTTP 读取 `60/100`、6 个维度与 `deterministic_local` 测试标识；详情链接闭合到本期报告；撤回后同一客户立即得到 `snapshot=null`，页面显示「暂不评分」，不保留 60 分。
- supervisor 同时核 PostgreSQL 中该客户恰有 1 条快照，score/max/evidence_mode 分别为 `60/100/deterministic_local`；Ark 调用为 0。
- 首轮启动前失败：系统 Python 缺 `psycopg`，未启动 Docker；后续统一使用项目 `.venv/bin/python3`。
- 两个真实失败均为旧 runner 与新响应式 UI 不一致：客户列表定位只认 `<tr>`；客户登录仍等待旧 `/portal/qa`；移动端报告导航只取隐藏的第一个同名链接。均只修 runner，未放宽业务断言。
- 最终标准门：`analysis-report-workflow-uat-check` exit=0，尾码 `LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK`；`section_count=7`、`citation_count=2`、`health_http_score=60`、`health_detail_dimensions=6`、`health_null_after_withdraw=1`、`dedicated_c/v/n=0`、`shared_match=1`、`skipped=0`。
- 503 不回退与任一本地开关关闭即 `snapshot=null` 已由前端合同和 PostgreSQL/HTTP 运行时门证明，本次浏览器门不冒充这两个故障注入场景。
- checkpoint 首次 `git diff --cached --check` 仅报健康度冻结合同 3 行 Markdown 尾随双空格；精确替换为等价 `<br>`，SHA256 由 `8e38b944…2296` 更新为 `44136a82…bfa`，未改字段、状态码或权限语义。
- 标签：HEALTH_SCORE_BROWSER_WORKFLOW_UAT_PASSED / DETERMINISTIC_LOCAL_SCORER_ONLY / BACKEND_CHECKPOINT_READY / NOT_PRODUCTION。下一步仅创建本地 checkpoint，不 push。

## 2026-08-24｜Codex 接续：撤回与评估时间反向门收口

- 隐藏审计发现两处测试盲区：撤回断言允许返回其他旧版本；库内 `published_at` 已查询但未与 payload `assessed_on` 比较。
- 最小修复：`latest_health` 将 `assessed_on` 与权威 `published_at` 的 UTC canonical 值闭合，并同时核 `max_score`；SHA 正确但评估时间错仍返回 503。
- 撤回门改用本周期尚无报告、但已有合法材料的 `race` 客户；临时交换 B/C audience binding，撤回后严格断言 `snapshot=null`，finally 恢复绑定。未放宽生产合同。
- PG 周期1：`Ran 26`，25 绿、1 error；原因是最初选的 `unbound` 客户没有可生成材料，`ReportNotFound` 属夹具选择错误。未改生产生成资格。
- PG 周期2：`Ran 26 tests in 9.192s / OK`。新增 `test_sha_ok_assessed_on_wrong_is_503` 通过。
- 最终组合门（本次第3个且最后一个 PG 周期）：`Ran 73 tests in 9.261s / OK`，exit=0，skipped=0。
- 仍有既有 `StarletteDeprecationWarning`（TestClient/httpx 兼容债），不影响本门；未安装或升级依赖。
- 标签恢复：HEALTH_SCORE_CONTRACT_PASSED / HEALTH_SCORE_FRONTEND_CONTRACT_PASSED / HEALTH_SCORE_POSTGRES_RUNTIME_PASSED / HEALTH_SCORE_HTTP_RUNTIME_PASSED / DETERMINISTIC_LOCAL_SCORER_ONLY / BROWSER_REVALIDATION_PENDING / BACKEND_CHECKPOINT_READY / NOT_COMMITTED / NOT_PRODUCTION。

## 2026-08-23 23:55｜续会话收口（组合门原始 stdout）

- 断线后本会话无法取出上一会话 unittest 原始 stdout。拍板「最多 3 个 PG 周期」让位于真实性：再跑一次最终组合门，以便对话可贴原始输出。
- 命令：`PYTHONPATH="$PWD/src:$PWD" F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan` + 指定 venv python `-B -m unittest` 六模块 `-v`。
- 原始结果：`Ran 72 tests in 9.305s` / `OK` / skipped=0 / EXIT=0。tearDown 仍硬断言 CLEAN。
- 收口实测：专属 C/V/N=0/0/0；共享指纹 SHA256=`f08864aa2b34d9ddc9f98f114590a0a8b58eeba0e7c5c7a989adf45881b0d065`（6852B）与任务0 一致；白名单外相对任务0 TSV drift=0；冻结四件 SHA 命中；`git diff --check=0`；staged=0；`src/web/node_modules` 不存在。
- 未 commit/push/deploy。BLOCKED 仍为「无」。
- 标签：HEALTH_SCORE_CONTRACT_PASSED / HEALTH_SCORE_FRONTEND_CONTRACT_PASSED / HEALTH_SCORE_POSTGRES_RUNTIME_PASSED / HEALTH_SCORE_HTTP_RUNTIME_PASSED / DETERMINISTIC_LOCAL_SCORER_ONLY / BROWSER_REVALIDATION_PENDING / BACKEND_CHECKPOINT_READY / NOT_COMMITTED / NOT_PRODUCTION

## 2026-08-23 23:40｜任务3 PG 门 + 最终组合门

- PG 周期1红：`force_rls_names` 全库 56≠42（判据改为闭集 42 是子集）；flags=0 时 generate 触发 `GenerationDisabled`（改为 approve 时开旗、publish/读时关旗）。
- PG 周期2绿：`tests.test_analysis_report_postgres_integration` `Ran 25, OK`（模块≥23）。
- 最终组合门（含 PG 周期3）：`Ran 72 tests in 9.609s OK`，skipped=0，exit=0。tearDown 硬断言 CLEAN / C/V/N=0 / 控制目录不存在 / shared_match=1。
- 已证：f1_0018 + 闭集 42 FORCE RLS；publish+snapshot+两类 audit 同事务；非法/抛错/INSERT 失败回滚 approved；关开关不写不读旧分；client 可读，provider/跨租户/revoke/withdraw 不可读；SHA 对身份错与篡改 503；快照不可 UPDATE/DELETE；f1_api 仅 SELECT/INSERT；真实 ASGI GET；Ark/外网=0。
- tsc `--noEmit -p src/web/tsconfig.app.json` exit=0；临时 node_modules 链接已删。
- 冻结四件 SHA 命中；白名单外相对任务0 TSV drift=0；`git diff --check=0`；staged=0；专属 C/V/N=0；共享指纹 SHA256=`f08864aa2b34d9ddc9f98f114590a0a8b58eeba0e7c5c7a989adf45881b0d065`（6852B）。
- 标签：HEALTH_SCORE_CONTRACT_PASSED / HEALTH_SCORE_FRONTEND_CONTRACT_PASSED / HEALTH_SCORE_POSTGRES_RUNTIME_PASSED / HEALTH_SCORE_HTTP_RUNTIME_PASSED / DETERMINISTIC_LOCAL_SCORER_ONLY / BROWSER_REVALIDATION_PENDING / BACKEND_CHECKPOINT_READY / NOT_COMMITTED / NOT_PRODUCTION
- 证据包：`…/material-rag-analysis-report-visual-polish-20260823/.health-score-verify-20260823-final/`（0700/0600）。未 commit/push/deploy。

## 2026-08-23 23:20｜任务2 运行时封口

- `latest_health`：非 client → 404；任一本地开关关闭 → 200+null，不读库中旧 `deterministic_local` 分。
- repository JOIN 权威 version 身份；SHA 对但身份不符 → 503。
- `_store_health_snapshot` 对 scorer 输出再 `validate_snapshot`。
- f1_0018 INSERT 要求 published+artifact_ready；JSONB object；UPDATE/DELETE 仍 trigger 拒绝。
- 后端 `_closed` 键序；前端真实日历 + 六键 union；latest 稳定排序，新发布无快照不回退旧分。

## 2026-08-23 22:55｜任务1 合同先红后绿

- 后端合同 16 项、前端合同 10 项；closeout 允许 f1_0014..f1_0018，analysis-report 目标 f1_0018，authz FORCE RLS 42。
- 第一次有效红灯（判据已冻）：`validate_snapshot` 错序、维对象错序、前端 Feb30、缺 `landHealthIfCurrent`。
- 实现：`_closed` 改为 list 键序；`assessed_on` 验真实日历；`landHealthIfCurrent` + 页面/HTTP mapper；`latest_health` client 门 + 双开关关则 null。
- JSONB 不保序：入库 `from_storage=True` 先 canonicalize。理由：身份与合同闭合优先于 JSON 字节序。
- 命令：`PYTHONPATH="$PWD/src:$PWD" F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan ... unittest` 五模块 `Ran 47, OK, skipped=0`。
- 标签：HEALTH_SCORE_CONTRACT_PASSED / HEALTH_SCORE_FRONTEND_CONTRACT_PASSED / DETERMINISTIC_LOCAL_SCORER_ONLY。下一步任务2已随实现落地，任务3 PG 门。

## 2026-08-23 22:43｜本轮开工回执（正常验证）

- HEAD `6cdbba30b6c4a278a9d3de89f0d243c201291d52`，分支 `codex/material-report-aeco-polish`；dirty=65（36M+29??），staged=0，D/R=0，diff-check=0。
- 专属 C/V/N=0/0/0，共享指纹 SHA256=`f08864aa2b34d9ddc9f98f114590a0a8b58eeba0e7c5c7a989adf45881b0d065`（6852B）；与任务书 65 路径相符。
- 冻结四件 SHA 命中：合同 `8e38b944…2296`、generator `280b9618…acb3`、f1_0017 `f23f371f…1c16`、local_browser_fixture `3c90dd46…3fcd`。
- 现役门：closeout 11 + authz 5 + PG 15 = 31；健康度专项测试 0。旧门仍锁 f1_0017 / 41 表；实现已指向 f1_0018。
- 本轮：合同先红后绿 → 封运行时泄漏 → 一个真实 PG 周期证明事务/RLS/HTTP；不实现真实评分器。
- 证据包：`…/material-rag-analysis-report-visual-polish-20260823/.health-score-verify-20260823/`（0700/0600）。下一步任务1。

## 2026-08-23 19:36｜任务0 开工回执

- 目标：把门户健康度从「前端演示分」推进到「后端不可变快照 + GET /api/v1/analysis-reports/health/latest」；生产无评分器时 HTTP `snapshot=null`，页面只显示「暂不评分」。
- 顺序：冻结合同 → f1_0018 单表/RLS/不可变触发器 → 发布同事务插入快照 → 前端 HTTP 真接线 → 一次静态检查。
- 最大风险：工作区切换曾把 47 个 dirty 收入 `stash@{0}`（19:33:47）；已 `stash apply` 未 drop。恢复后 porcelain=23M+24??、staged=0、D/R=0、diff-check=0，与任务书相符。
- 取舍：身份/真实性 > 事务一致性 > 完整 > 速度。不改测试、不开 Docker/浏览器/完整 build、不 commit。
- HEAD `6cdbba30b6c4a278a9d3de89f0d243c201291d52`，分支 `codex/material-report-aeco-polish`。下一步任务1冻结合同。

## 2026-08-23 19:45｜任务1 冻结合同

- 写入 `A_ECO_MANAGEMENT_HEALTH_API_CONTRACT.md`。
- Envelope exact-key：`schema, snapshot`；`schema=anhuan-analysis-report-health-v1`。
- 无绑定/无发布/无快照/已撤回 → 200 + `snapshot=null`；完整性/DB 错误 → 503，禁止回退旧分。

## 2026-08-23 20:10｜任务2 后端快照

- 新增 `f1_0018`（down_revision=f1_0017，未改 f1_0017 文件）。
- 单表 `analysis_report_health_snapshot`：JSONB payload + payload_sha256；复合 FK；每 version 唯一；FORCE RLS；admin SELECT/INSERT；client 经 active audience 且 version published+artifact_ready；PUBLIC/f1_worker REVOKE ALL；无 UPDATE/DELETE；不可变 trigger。
- 独立 `HealthScorerPort` + `FakeDeterministicHealthScorer`，不引用 generator。
- 仅双开关 `generation_enabled()` 时，publish 同一 session：transition → insert snapshot → `health_snapshot_created` audit → 一次 commit。
- `GET /health/latest` 只读当前最新 published；无快照 200+null；SHA/score 不符或 DB 错误 503。
- 专属 head=f1_0018；FORCE RLS 闭集 42；默认工程仍 f1_0014；material-RAG 仍 f1_0016。

## 2026-08-23 20:25｜任务3 前端接线

- `AnalysisReportApi.getLatestManagementHealth()`；HTTP 调 `/v1/analysis-reports/health/latest`；wire exact-key/UUID/ISO/六维/求和。
- Mock 才返回 `SYNTHETIC_MANAGEMENT_HEALTH`；HTTP 无 mock/60 回退。
- 首页评分独立 loading/error/retry，不遮蔽报告；详情 loading/error/retry + tenant epoch。
- `snapshot=null` →「暂不评分」；`deterministic_local` →「测试环境·确定性评分」。

## 2026-08-23 20:28｜任务4 静态检查

- 仓库内无 `.venv` / `src/web/node_modules`。复用 `/Users/lichenhao/Desktop/安环项目/.venv/bin/python3` 与 `/Users/lichenhao/Desktop/安环项目/src/web/node_modules/.bin/tsc`；`src/web/node_modules` 临symlink，检查后已删除。
- 第1次组合：ast.parse 14 文件 exit=0；tsc exit=0；`git diff --check` exit=2（repository.py EOF 多一空行）。已修。
- 第2次组合：ast.parse exit=0；tsc `-p src/web/tsconfig.app.json --pretty false` exit=0；`git diff --check` exit=0；wall=1.617s；staged=0。
- 原 47 dirty 全在；额外 18 个均为本轮白名单新建/修改。白名单外零漂移。f1_0017 未改。未 commit。

## 反向审计（摘）

- 生产无双开关：publish 不插快照；latest 对无快照返回 null，不读旧 version。
- HTTP adapter 不含 60/SYNTHETIC/mock。
- 评分器与 generator 无 import 关系。
- RLS：GRANT 仅 SELECT,INSERT；REVOKE PUBLIC 与 f1_worker；FORCE RLS；immutable trigger。
- 闭集 42 = P2–P7 31 + material-rag 3 + analysis-report 8。
- BLOCKED：无。
