# P3 CONTROLLED INGESTION Blocked / 技术债

## 当前阻断

- 无需用户决策的当前实现阻断；状态`SMOKE_PASSED / NOT_RELEASE_VERIFIED`。

## 延后问题

| reason | 影响面 | 建议修复点 | 状态 |
| --- | --- | --- | --- |
| 旧`/api/v1/documents/upload`后端仍为Fixture兼容入口 | 直接调用旧内部API仍不经过P3；产品路由和菜单已移除该入口 | 保持仅作既有Fixture兼容，后续若对外开放必须转入受控链 | INTERNAL_ONLY |
| 当前前端没有PDF/Office渲染依赖 | 不能在浏览器直接安全解析复杂文档 | 已采用后端低保真分页文本JSON、sheet grid和validated JPEG；不执行浏览器文档主动内容 | BOUNDED |
| 固定ClamAV sidecar运行接缝 | 已在UUID隔离scratch以固定digest完成真实ClamAV+MinIO happy path，未覆盖签名在线更新或生产容量 | 保持本地固定签名、fail-closed及资源上限；生产签名更新另行授权 | CLOSED_TARGETED_SMOKE |
| P3允许格式窄于旧storage白名单 | 旧入口仍可能接受DOC/XLS/PPT/PNG | P3入口精确限制PDF/DOCX/XLSX/JPEG；旧入口不暴露于产品UI | PLANNED |
| 安全的后台quarantine claim需要新的受限权限入口 | 现阶段没有自动后台恢复，用户需显式process/retry | 原型使用API显式处理token+lease CAS；未来如需后台化，单独设计quarantined-only claim，不得把未扫描对象提前标ready | DEFERRED_ARCHITECTURE |
| `document_preview_unit`保留了精确worker lease策略，但当前显式处理器用canonical manifest对象，不写该表 | 预览元数据当前由任务摘要+manifest承载，表为后续后台化预留 | P3原型不扩大API持久化写权；未来后台化时再启用精确lease写入 | DEFERRED_ARCHITECTURE |
