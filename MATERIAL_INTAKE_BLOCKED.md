# MATERIAL INTAKE Blocked

## 当前阻断

- 无已知代码实现阻断。实库已从 `f1_0011` 迁移到 `f1_0014`；同一份确定性合成文本 PDF 分别在服务公司域和客户域走通 PostgreSQL、MinIO、ClamAV、材料分析、释放、上下层知识域 RLS 及公司政策硬门，当前为 `SMOKE_PASSED / NOT_PRODUCTION`。
- 首次 `f1_0013` 迁移因旧文档回填受 `FORCE RLS` 遮蔽而失败，事务已整体回滚；限定 bootstrap session 的有界 `RESET ROLE` 回填修复后完整重跑通过。它是已关闭的迁移缺陷，不是遗留阻断。
- 浏览器、真实 Demo PDF、物理 RAG、OCR、候选准确率、备份恢复和发布验收均未执行，不得从本次 smoke 推导为已通过。

## 固定延后边界

- `pdf-inspector 0.2.6` 不进入运行时；等待 patched、pinned、可复核构建后另行启用 shadow。
- 扫描型 PDF 当前只生成 OCR 路由提示，不执行 OCR。
- 没有持久后台队列；请求中断后的材料用现有 process/retry 恢复。
- 报告标题、日期和摘要会生成候选，但本轮不自动选择服务任务或创建 P4 报告；该归属仍需人工判断。
- 本轮没有建立物理 RAGFlow dataset、索引或检索链；`service_provider/client` 只是供应商中立的知识域真值与权限边界。物理 RAG 的现役阻塞见 [MATERIAL_RAG_BLOCKED.md](./MATERIAL_RAG_BLOCKED.md)，不记入本切片完成门。
