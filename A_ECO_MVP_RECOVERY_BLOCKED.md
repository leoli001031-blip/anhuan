# A_ECO_MVP_RECOVERY_BLOCKED

## 当前工作树已解除（`TARGETED_TEST_PASSED / WORKFLOW_UAT_PASSED`）

1. **CRM 客户粒度服务事项**：provider 列表以 `client_account_id` 过滤并校验返回归属，客户工作台已恢复「服务事项」路由。
2. **client-safe 门户 DTO**：门户只请求 client-safe 端点，前端对 `id/title/service_type/status/planned_start_at/planned_end_at/assigned/updated_at` 八个字段做精确闭集校验；额外字段 fail-closed，不持有 provider description/findings/timeline。

上述已经过离线合同、真实 PostgreSQL 和全自动浏览器验证；最终浏览器摘要为 `client_services=1 / client_qa=1 / qa_citation_count=2 / ark_calls=0 / mock_data=0 / skipped=0`。这是本地合成数据证据，不是真实客户或发布验收。

## 仍存的产品边界

1. **client audience 开通合同未对正式 HTTP 开放**：「新建客户」仍只能在明确演示边界使用，不能声称已形成正式 audience provisioning 流程。
2. **报告生成资格的索引信号未暴露前端**：「入库处理完成」只陈述入库生命周期，生成资格仍由后端在生成时校验；前端不推导、不伪造资格状态。
3. **发布边界仍未开放**：本地机器门已通过，但仍为 `HUMAN_VISUAL_ACCEPTANCE=NOT_PART_OF_THIS_RUN / NOT_COMMITTED / NOT_PUSHED / NOT_DEPLOYED / NOT_PRODUCTION`，且没有真实远端参数或客户数据。
4. **异步恢复边界**：DB commit 到 RQ enqueue 之间尚无持久 outbox/sweeper，目前依赖持久 request ID 恢复；排队后 actor membership 被撤销时尚无 actor-independent 终态化。
5. **客户上下文仍是唯一性合同**：存在多个 active provider/client audience 时按恰好一个失败关闭；本地抽取候选最多 256 个 unit，大语料召回尚未验证。
