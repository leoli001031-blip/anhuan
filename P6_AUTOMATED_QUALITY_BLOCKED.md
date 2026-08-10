# P6 AUTOMATED QUALITY Blocked / 技术债

## 当前阻断

- 无需用户决策的当前阻断；状态`SMOKE_PASSED / NOT_RELEASE_VERIFIED`。

## 延后问题

| reason | 影响面 | 建议修复点 | 状态 |
| --- | --- | --- | --- |
| Oracle仅使用合成观察值 | 不能代表真实文档、真实OCR或真实问答准确率 | 后续经专业Gold与真实UAT授权后另建评估集 | NON_GOLD |
| 不调用双parser/OCR/provider | 分歧记录只验证工作流与指标合同，不验证真实引擎差异 | 正常验证/外部资源授权后接真实shadow输入 | NO_EXTERNAL_CALLS |
| 不做自动回归门或发布阻断 | P6结果当前不影响部署或生产开关 | 发布治理留给未来明确的发布验收 | NOT_RELEASE_GATE |
| 场景在已有历史run后仍可编辑，result未保存当时的scenario SHA | 旧run结果不可仅凭当前场景配置重算，但已保存的result/evidence digest仍不可变 | 后续版本化scenario，或在result中固化scenario_sha256；本原型不扩DDL | DEFERRED_NON_BLOCKING |
