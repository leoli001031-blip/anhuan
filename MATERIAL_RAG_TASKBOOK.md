# MATERIAL RAG 任务书

把 `service_provider/client` 两类受控知识域接入独立物理 RAG。公司查询只搜索公司共享域；客户查询只组合公司共享域与当前客户域。索引只接收已释放、扫描干净、预览完成的版本，检索结果经 PostgreSQL 重新核对知识域、版本、页码和正文摘要后才返回。

本轮使用四份内部 Demo PDF 验证文本、扫描、混合和长文档。原件只读且不进入仓库。最近一次重放的授权范围是：四份 Demo 经本地解析和 PII 去除后的 canonical 文本、固定无 PII 的 provider/client 合成 canary、范围检索／幂等／引用验证所需的固定查询文本，以及验证器明确登记的其他无 PII 合成文本。所有正文必须先经过本地敏感信息过滤，以 SHA 清单追溯，并且只能经 endpoint-aware relay 发送到固定 Ark embedding 模型。真实客户数据、未经登记的用户自由输入、PDF、页面图片、原文件名、对象键、本机路径、外部 LLM、外部 OCR、pdf-inspector、自动发布和共享栈仍禁止；上述授权不自动延续到下一次运行。

历史“三轮即停”规则曾由用户逐次给出字面例外。2026-08-15 字面授权的完整 `material-rag-verify` 额度 2/2 已用尽。两次均固定停在 `LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED`（exit 2）；验证器已到达。再次跑 `./scripts/localctl material-rag-verify` 或发送 allowlist 正文必须取得新的字面授权。专属 container、volume、network 残留均为 0。授权范围内仅临时停止并已恢复共享 `anhuan-f1-ragflow-1`；未触碰其他共享容器、浏览器、真实数据和发布边界。

当前状态为 `TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`；只有完整命令取得 `LOCAL_MATERIAL_RAG_VERIFY_OK`，且真实反向隔离、引用、重建、删除与聚合外发审计门全部通过后，才能记为 `SMOKE_PASSED / NOT_PRODUCTION`。
