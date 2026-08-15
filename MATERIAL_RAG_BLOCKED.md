# MATERIAL RAG Blocked

## 2026-08-15｜当前阻塞

- 本切片授权的 `./scripts/localctl material-rag-verify` 额度已用尽（2/2）。两次均 exit 2，最终固定码均为 `LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED`。验证器已到达（verifier `exited/1`）。无 `LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE`；无预览 `error_reason`（停在扫描预检/扫描门，未到预览）。
- 两次 cleanup 前快照一致：`material-rag-clamd` 为 `running` / `health=starting` / `restart_count=1` / `oom_killed=false`；专属 ragflow、ocr 及其他依赖为 healthy；verifier exited 1。无 OCR/RAGFlow 137。
- 第1次后按该唯一快照做了白名单内最小修复（预检重试 PROTOCOL；clamd healthcheck retries 3→60）。第2次墙钟仍约 249s（未拉长约 60s），同一码再现，不能再把主因收窄为「预检未重试 PROTOCOL」或「healthcheck retries=3」。
- 未验证：整栈预览、索引、检索、反向隔离、引用、重建、删除、聚合外发审计、Ark embedding 实发是否发生。不能记 `LOCAL_MATERIAL_RAG_VERIFY_OK` 或 `SMOKE_PASSED`。
- 专属 container/network/volume 残留 0。共享 `anhuan-f1-ragflow-1`（`fe698b0db22d`）已恢复 running/healthy；其余共享容器 running/exit 未变。F0-I key 未读、未改、未替换。未改 Docker Desktop、共享库或默认迁移。
- 指定锁定环境 `/private/tmp/f1lockvenv2` 全量 unittest 仍为 Ran 40 / errors=9（0 个 `.py`）；未装依赖。既有项目 venv 为 40/OK，不替代专属镜像内 unit。
- 再次跑完整 verify 或外发 allowlist 正文必须取得新的字面授权。

当前状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。
