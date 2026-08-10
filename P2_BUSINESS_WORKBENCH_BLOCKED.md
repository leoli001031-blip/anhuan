# P2 BUSINESS WORKBENCH Blocked / 技术债

## 当前阻断

- 无。

## 非阻断问题登记规则

只登记`reason / 影响面 / 建议修复点 / 首次发现Wave`。四Wave后的集中收口已完成合并去重；当前无阻断项，以下均为明确延后的非阻断技术债。

| reason | 影响面 | 建议修复点 | 首次发现Wave | 状态 |
| --- | --- | --- | --- | --- |
| 旧页面仍向统一API helper传入`/api/v1/...`，会形成重复前缀 | 企业、文档等既有页面；P2主链已使用正确`/v1/...`，租户选择入口已做最小修正 | 后续维护阶段统一规范旧页面API路径并加轻量client合同检查 | Wave 1 | DEFERRED |
| 现有成员目录没有安全的租户内展示名字段 | 分配抽屉暂以成员角色与opaque用户ID短码展示，不影响分配主链 | 后续在受控身份目录中增加显示名并保持租户范围 | Wave 1 | DEFERRED |
| 全局页面样式仍有旧的居中与系统暗色混用 | P2页面以局部左对齐和Ant Design组件规避，整体视觉一致性有限 | 后续产品视觉收口统一应用壳层token与响应式布局 | Wave 1 | DEFERRED |
| P2四条smoke最初仅为离线domain/API合同 | 已完成一次性随机PostgreSQL、真实`f1_api`、FORCE RLS与ASGI API持久化主链；跨租户读写、timeline/audit/notification与精确清理全部为0；真实OIDC和并发revision仍不在本轮范围 | OIDC或专项并发仅在后续明确授权时独立验证，不恢复F1.1.1发布验收 | Wave 1-3 | RESOLVED_FOR_P2_SMOKE |
| 前端目标build产生大于500kB的单chunk告警 | 首屏下载体积与后续页面增多时的加载性能 | 后续性能轮按业务路由做lazy loading/code splitting | Wave 1 | DEFERRED |
