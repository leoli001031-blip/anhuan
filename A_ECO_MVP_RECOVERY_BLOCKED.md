# A_ECO_MVP_RECOVERY_BLOCKED

三个后端合同缺口（本轮确认仍在，功能 fail-closed 隐藏）：

1. **缺 CRM 客户粒度的服务事项过滤**：P2 service-case/finding/calendar 按企业租户组织，无 `client_account_id` 参数。运营台客户页不再把「服务事项」作为正式主 tab（已移除 tab 与路由），不展示可能串域的列表。
2. **缺 client-safe 服务详情 DTO**：通用 P2 详情含内部字段（操作人 ID、原始事件类型等）。门户服务事项只保留列表（标题/类型/统一状态/期限/逾期），不请求、不展示 description/findings/timeline。
3. **缺明确的 client audience 开通合同**：正式 HTTP 不提供「新建客户」入口（仅演示环境可见）。

另保留一项信号缺口：

4. **报告生成资格的索引信号未暴露前端**：材料页不再出现任何资格推导；「入库处理完成」只陈述入库生命周期，生成资格由后端在生成时校验（不合格源被 404 拒绝）。

自动化验收债（不影响本轮人工双身份与报告工作流结论）：

5. **历史浏览器 runner 与新信息架构不一致**：dual-identity runner 仍等待已隐藏的 `/portal/qa`；workflow runner 在默认 800px 视口仍只查桌面表格 `<tr>`，未识别响应式客户列表。因此 `analysis-report-uat-check` 与 `analysis-report-workflow-uat-check` 本轮不能写成通过，需后续仅更新 runner 选择器与期望路由后再验。
