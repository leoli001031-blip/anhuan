# MATERIAL INTAKE Blocked

## 当前阻断

- 无代码实现阻断。`f1_0011` 实库迁移与一份合成文本 PDF 的 PostgreSQL、MinIO、ClamAV、材料分析、人工确认及跨租户 RLS 主链已通过，可标记 `SMOKE_PASSED`；浏览器批量流程、真实材料和准确率仍未验证，不得标记 release verified。

## 固定延后边界

- `pdf-inspector 0.2.6` 不进入运行时；等待 patched、pinned、可复核构建后另行启用 shadow。
- 扫描型 PDF 当前只生成 OCR 路由提示，不执行 OCR。
- 没有持久后台队列；请求中断后的材料用现有 process/retry 恢复。
- 报告标题、日期和摘要会生成候选，但本轮不自动选择服务任务或创建 P4 报告；该归属仍需人工判断。
