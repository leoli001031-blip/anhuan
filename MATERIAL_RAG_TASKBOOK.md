# MATERIAL RAG 任务书

把 `service_provider/client` 两类受控知识域接入独立物理 RAG。公司查询只搜索公司共享域；客户查询只组合公司共享域与当前客户域。索引只接收已释放、扫描干净、预览完成的版本，检索结果经 PostgreSQL 重新核对知识域、版本、页码和正文摘要后才返回。

本轮使用四份内部 Demo PDF 验证文本、扫描、混合和长文档。原件只读且不进入仓库。最近一次重放的授权范围是：四份 Demo 经本地解析和 PII 去除后的 canonical 文本、固定无 PII 的 provider/client 合成 canary、范围检索／幂等／引用验证所需的固定查询文本，以及验证器明确登记的其他无 PII 合成文本。所有正文必须先经过本地敏感信息过滤，以 SHA 清单追溯，并且只能经 endpoint-aware relay 发送到固定 Ark embedding 模型。真实客户数据、未经登记的用户自由输入、PDF、页面图片、原文件名、对象键、本机路径、外部 LLM、外部 OCR、pdf-inspector、自动发布和共享栈仍禁止；上述授权不自动延续到下一次运行。

2026-08-18 合同漏项窗口：默认运行目标仍是 `f1_0014 / 35表`，专属 material-RAG 仍是 `f1_0015 / 38表`。wave2/3/4 源码 head 漏项与不可哈希 target 已修。新检查点 `/Users/lichenhao/Desktop/安环项目/artifacts/material-rag-engineering-checkpoint-20260818`，patch/root/包级总root 见该目录 `RESULT.v2.txt`。旧 `.../material-rag-engineering-checkpoint-20260817` 只读。本轮未运行 Docker；沿用此前 clean-clone `523069.978ms / LOCAL_MATERIAL_RAG_VERIFY_OK`，不是本轮重跑。不 commit/push。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-18 检查点窗口：默认工程精确停在 `f1_0014 / 35表`，专属 material-RAG 精确使用 `f1_0015 / 38表`。合并回归 `Ran 164 / OK`。隔离 clone 完整 `./scripts/localctl material-rag-verify` 已取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH`。检查点只写 `artifacts/material-rag-engineering-checkpoint-20260817`。不 commit/push。`ARK_KEY_ROTATION_REQUIRED`。

2026-08-17 20:39 窗口字面授权最多 40 次完整 `./scripts/localctl material-rag-verify`（旧 conflict v1–v10 不计入），连续自治最长 12 小时。已用 3 次后 v3 取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`，`IMAGE_SOURCE=MATCH`，回归 `Ran 45 / OK`。当时写成 `SMOKE_PASSED / NOT_PRODUCTION`。不 checkout。不 commit/push。

2026-08-17 15:01 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 10 次后额度耗尽。任务1 冲突探针已绿。v7 rebuild job 已成功后 `_remote_snapshot` 误用无 scope 默认检查；v8–v10 verifier 已输出 JSON+`VERIFY_OK`，localctl `OUTPUT_INVALID`，v10 `line_classes=OTHER|JSON|VERIFY_OK|OTHER`。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 14:10 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 3 次后因同一完整签名第三次停止。任务1 known-version snapshot 已绿。v1–v3 同一 INTERNAL `DB_SNAPSHOT_EXIT`/`42501`，证据字节相同。未跑 v4。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 03:18 窗口字面授权最多 10 次完整 `./scripts/localctl material-rag-verify`，已用 5 次后因同一完整签名第三次停止。任务1 egress 转印已绿。v1 `CHUNK_ADD_CODE_400`+egress upstream 全 0；v2 Ark 2xx=286/`REMOTE_SNAPSHOT`；v3–v5 同一 INTERNAL `DB_SNAPSHOT`/`42501`。未跑 v6。当时未写成 `SMOKE_PASSED`。不 checkout。

2026-08-17 02:06 窗口字面授权最多 6 次完整 `./scripts/localctl material-rag-verify`，已用 5 次后停止。v1 `MATERIAL_VERSION_NOT_INDEXABLE` 已越过；v2 `MATERIAL_RAG_UNAVAILABLE`；v3 `MATERIAL_RAG_PROBE_FAILED`；v4 `CHUNK_ADD_FAILED_200`；v5 `CHUNK_ADD_CODE_400`。未跑 v6。当时未写成 `SMOKE_PASSED`。不 checkout。

当前状态为 `TARGETED_TEST_PASSED / SMOKE_PASSED / DUAL_F1_MIGRATION_CONTRACT_PASSED / CHECKPOINT_READY / NOT_PRODUCTION`。当前分支精确为 `codex/material-rag-scanner-protocol`。不是 `RELEASE_VERIFIED`，未部署、未生产。`ARK_KEY_ROTATION_REQUIRED`。
