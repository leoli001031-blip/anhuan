# P4 VIEWS / REPORTS / CRM Blocked / 技术债

## 当前阻断

- 无需用户决策的当前阻断；状态`SMOKE_PASSED / NOT_RELEASE_VERIFIED`。

## 延后问题

| reason | 影响面 | 建议修复点 | 状态 |
| --- | --- | --- | --- |
| P3 ClamAV/数据库/对象存储happy path | UUID隔离scratch最小链已通过，但不等同共享栈或生产容量 | 保留当前smoke证据；发布验收仍关闭 | CLOSED_TARGETED_SMOKE |
| P4 artifact只登记canonical JSON数据库快照元数据 | 当前没有正式PDF/HTML报告或外部文件下载 | 正式渲染、签发、模板和发布留在明确后续授权，不在P4伪造 | BOUNDED |
| CRM字段可承载联系人信息但真实客户数据未授权 | 当前只能使用内部合成/fixture数据 | 真实客户导入、数据留存政策、UAT和通知另行开闸 | CLOSED_REAL_DATA |
| P4不做跨企业总盘 | 多企业管理员需逐企业切换查看 | 后续若需要跨企业经营总览，单独设计授权聚合而非绕过RLS | DEFERRED_PRODUCT |
| P4定向与实库验证 | 定向8/8及UUID实库/API/RLS最小链已通过 | 继续保持非发布状态；真实客户/UAT另行授权 | CLOSED_TARGETED_SMOKE |
