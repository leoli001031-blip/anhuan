# P5 POLICY WORKFLOW Blocked / 技术债

## 当前阻断

- 无需用户决策的当前阻断；状态`SMOKE_PASSED / NOT_RELEASE_VERIFIED`。

## 延后问题

| reason | 影响面 | 建议修复点 | 状态 |
| --- | --- | --- | --- |
| 不联网采集法规源 | 来源和版本均由内部人工/fixture登记，不能证明外部来源实时性 | 真实法规源接入、抓取频率、许可与网络授权另行开闸 | CLOSED_EXTERNAL |
| effect_status和impact均为人工候选 | 不能作为法规适用或专业合规结论 | 正式内容、地区行业判断与专业签发留给真实UAT/专业人员 | PROFESSIONAL_REVIEW_REQUIRED |
| 仅结构化元数据检索 | 不支持全文、向量或跨域语义检索 | 后续在受控内容和质量Oracle边界明确后独立设计 | DEFERRED_SEARCH |
| P3-P5目标运行验证 | UUID隔离scratch最小链已通过，仍不是共享栈、真实法规源或发布验收 | 保留smoke边界；真实来源/UAT另行开闸 | CLOSED_TARGETED_SMOKE |
