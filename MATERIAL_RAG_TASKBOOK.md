# MATERIAL RAG 任务书

2026-08-19 发布授权：提交并推送当前后端检索信任边界检查点，创建草稿 PR；不部署。Ark key 轮换不再作为其他开发的现役 blocker，真实 live retrieval 改记 `LIVE_RETRIEVAL_UAT_DEFERRED / NOT_TESTED`，不冒充通过。现役 `BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`。

2026-08-19 收口：从 checkpoint `6dd4b9158af3f8eb15922fff5bc715c9a3848f68` 完成生产检索信任边界纯离线后端合同门。离线先红 `Ran 11 / FAILED (errors=8)` 后绿 `Ran 11 tests in 0.282s / OK`。主仓合并门一次 `Ran 89 tests in 1.581s / OK`；clean-clone 合并门一次 `Ran 89 tests in 1.628s / OK`。`git diff --check=0`。未改公共 QA 实现。未跑 headed UAT / Ark / Docker / 部署 / commit。不重跑 checkpoint UAT。现役 `BACKEND_RETRIEVAL_TRUST_BOUNDARY_PASSED / BACKEND_PUBLIC_QA_FAIL_CLOSED_PASSED / BACKEND_CLEAN_CLONE_REPRODUCIBLE / BACKEND_CHECKPOINT_READY / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `UAT_PASSED` / `RELEASE_VERIFIED` / `HUMAN_UAT_READY`。

2026-08-19 开工：从 checkpoint `6dd4b9158af3f8eb15922fff5bc715c9a3848f68` 做生产检索信任边界纯离线后端合同门。不跑 headed UAT / Ark / Docker / 部署 / commit。不重跑 checkpoint UAT。现役保持 `UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。目标标签尚未达成。

2026-08-18 23:22 收口：离线 unittest 先红 `Ran 63 / FAILED (failures=6)` 后绿 `Ran 63 tests in 5.314s / OK`（一跳后再绿 `5.329s / OK`）。fresh live 周期1 check exit 2 `UAT_TENANT_SWITCH_FAILED`；一跳后周期2 start/check/stop 均 exit 0，尾码 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`，J6 五字段全 1，`valid_tenant_count=2`。现役 `UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `HUMAN_UAT_READY`。未跑 open。不 commit/push/PR/部署。Ark 外发=0。

2026-08-18 22:45 收口（历史；租户 Select 挡住 live，已被 23:22 覆盖）：离线 unittest 先红 `Ran 58 / FAILED (failures=6)` 后绿 `Ran 58 tests in 3.034s / OK`。本窗口 live 2/2 用尽。末次 check 固定码 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_OPTION_MISSING`。周期1 为 `UAT_TENANT_SWITCH_FAILED`。未恢复机器门两标签。当时现役 `TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。不 commit/push/PR/部署。Ark 外发=0。禁止本窗口再 live。

2026-08-18 20:24 收口（历史；J6 清空假绿，已被 22:45 覆盖）：离线 unittest 先红 `Ran 53 / FAILED (failures=12, errors=1)` 后绿 `Ran 53 tests in 2.822s / OK`。当时唯一 live start/check/stop 均 exit 0，尾码 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。当时现役（历史）`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。未写 `HUMAN_UAT_READY`。未跑 open。不 commit/push/PR/部署。Ark 外发=0。

2026-08-18 19:43 收口（历史，旧窗口 live 2/2 已封存）：离线 unittest `Ran 48 / OK`。当时 live 周期 2/2 用尽。周期2 check 固定码 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED REQUEST_NOT_SENT`，证据 `journey=J6_FAIL_CLEAR request_seen=0 expected_phase=unavailable`。当时未恢复三个 UAT 通过标签。当时现役（历史）`TARGETED_TEST_PASSED / MATERIAL_RAG_UAT_BLOCKED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / NOT_PRODUCTION`。不 commit/push/PR/部署。Ark 外发=0。下一跳（已由 20:24 完成）：antd 6 option DOM click 提交 `fail.clear`。

2026-08-18 18:53 收口：离线 unittest `Ran 41 / OK`。旧窗口 live 周期 2/2 用尽。周期2 check 固定码 `LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_PHASE_MISSING`。未恢复三个 UAT 通过标签。当时现役同上 BLOCKED 串。不 commit/push/PR/部署。Ark 外发=0。

2026-08-18 双合法租户隔离与资源安全门：撤销三个 UAT 通过标签。两个合法租户必须用 local seed `ENTERPRISE_A=...00a`、`ENTERPRISE_B=...00b` 的真实 Keycloak membership。禁止随机非成员 UUID 冒充双租户。overlay/start/check/stop 必须校验专属资源身份，同名异主拒绝且不删除。start 输出安全 localhost `/qa`，localctl 内部读 0600 secret 打开已登录 headed Chrome。不 commit/push/PR/部署。Ark 外发=0。当时现役同上 BLOCKED 串。

把 `service_provider/client` 两类受控知识域接入独立物理 RAG。公司查询只搜索公司共享域；客户查询只组合公司共享域与当前客户域。索引只接收已释放、扫描干净、预览完成的版本，检索结果经 PostgreSQL 重新核对知识域、版本、页码和正文摘要后才返回。

本轮使用四份内部 Demo PDF 验证文本、扫描、混合和长文档。原件只读且不进入仓库。最近一次重放的授权范围是：四份 Demo 经本地解析和 PII 去除后的 canonical 文本、固定无 PII 的 provider/client 合成 canary、范围检索／幂等／引用验证所需的固定查询文本，以及验证器明确登记的其他无 PII 合成文本。所有正文必须先经过本地敏感信息过滤，以 SHA 清单追溯，并且只能经 endpoint-aware relay 发送到固定 Ark embedding 模型。真实客户数据、未经登记的用户自由输入、PDF、页面图片、原文件名、对象键、本机路径、外部 LLM、外部 OCR、pdf-inspector、自动发布和共享栈仍禁止；上述授权不自动延续到下一次运行。

2026-08-18 合成浏览器 UAT（历史，18:12 已撤销 `HUMAN_UAT_READY`；20:24 只恢复机器门两标签，仍为 `HUMAN_UAT_NOT_READY`）：把离线 Node 状态机升级为真实浏览器→本地后端→Keycloak 租户鉴权。UAT router 同时要求 `F1_MATERIAL_RAG_UAT_LOCAL=1` 与 `F1_LOCAL_ENGINEERING=1`，复用 `tenant_from_header` + 管理角色，删除 `X-Uat-Actor`。专属 overlay + `material-rag-uat-start/check/stop`。Live gate `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。当时现役（历史）`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。不得写成 `UAT_PASSED` / `RELEASE_VERIFIED` / production。不 commit/push。证据见 `MATERIAL_RAG_UAT_REPORT.md`。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-18 产品 UAT 机器门（历史）：从已提交 checkpoint `a72fdb186de2ab53f6c8d72983f1b24fc99dac1e` 做离线合成 catalog + 闭集 `query_id` 机器门。公共自由提问继续 fail-closed。真实 Ark 检索腿保持 BLOCKED。当时现役（历史）`UAT_MACHINE_GATE_PASSED / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`。不得写成 `UAT_PASSED` / `RELEASE_VERIFIED` / production。不 commit/push。证据见 `MATERIAL_RAG_UAT_REPORT.md`。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-18 合同漏项窗口：默认运行目标仍是 `f1_0014 / 35表`，专属 material-RAG 仍是 `f1_0015 / 38表`。wave2/3/4 源码 head 漏项与不可哈希 target 已修。新检查点 `/Users/lichenhao/Desktop/安环项目/artifacts/material-rag-engineering-checkpoint-20260818`，patch/root/包级总root 见该目录 `RESULT.v2.txt`。旧 `.../material-rag-engineering-checkpoint-20260817` 只读。本轮未运行 Docker；沿用此前 clean-clone `523069.978ms / LOCAL_MATERIAL_RAG_VERIFY_OK`，不是本轮重跑。不 commit/push。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-18 检查点窗口：默认工程精确停在 `f1_0014 / 35表`，专属 material-RAG 精确使用 `f1_0015 / 38表`。合并回归 `Ran 164 / OK`。隔离 clone 完整 `./scripts/localctl material-rag-verify` 已取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH`。检查点只写 `artifacts/material-rag-engineering-checkpoint-20260817`。不 commit/push。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-17 20:39 窗口字面授权最多 40 次完整 `./scripts/localctl material-rag-verify`（旧 conflict v1–v10 不计入），连续自治最长 12 小时。已用 3 次后 v3 取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH`，回归 `Ran 45 / OK`。当时写成 `SMOKE_PASSED / NOT_PRODUCTION`。不 checkout。不 commit/push。

2026-08-17 15:01 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 10 次后额度耗尽。任务1 冲突探针已绿。v7 rebuild job 已成功后 `_remote_snapshot` 误用无 scope 默认检查；v8–v10 verifier 已输出 JSON+`VERIFY_OK`，localctl `OUTPUT_INVALID`，v10 `line_classes=OTHER|JSON|VERIFY_OK|OTHER`。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 14:10 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 3 次后因同一完整签名第三次停止。任务1 known-version snapshot 已绿。v1–v3 同一 INTERNAL `DB_SNAPSHOT_EXIT`/`42501`，证据字节相同。未跑 v4。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 03:18 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 5 次后因同一完整签名第三次停止。任务1 egress 转印已绿。v1 `CHUNK_ADD_CODE_400`+egress upstream 全 0；v2 Ark 2xx=286/`REMOTE_SNAPSHOT`；v3–v5 同一 INTERNAL `DB_SNAPSHOT`/`42501`。未跑 v6。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 02:06 窗口字面授权最多 6 次完整 `./scripts/localctl material-rag-verify`，已用 5 次后停止。v1 `MATERIAL_VERSION_NOT_INDEXABLE` 已越过；v2 `MATERIAL_RAG_UNAVAILABLE`；v3 `MATERIAL_RAG_PROBE_FAILED`；v4 `CHUNK_ADD_FAILED_200`；v5 `CHUNK_ADD_CODE_400`。未跑 v6。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-18 23:22 历史状态为 `UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`，当时 HEAD `a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`。该窗口不是 `UAT_PASSED`，不是 `RELEASE_VERIFIED`，未部署、未生产；当时仍记 `ARK_KEY_ROTATION_REQUIRED`。live 周期2 取得 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。22:45 `UAT_TENANT_OPTION_MISSING` 与 20:24 清空假绿、旧 `REQUEST_NOT_SENT` / `UAT_PHASE_MISSING` 均为历史，不得合并计数。未写 `HUMAN_UAT_READY`。
