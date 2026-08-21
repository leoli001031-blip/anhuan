# 客户材料分析报告｜本地后端 MVP 设计

现役：`MATERIAL_RAG_REPORT_API_CONTRACT_FROZEN / NOT_PUSHED / NOT_PRODUCTION`。
P4 `business_report` canonical snapshot 原样保留，不改名、不复用表、不冒充本域。
本轮不做 PDF、Ark、外部模型、真实客户数据、生产部署或发布验收。

## 域

独立 schema 对象前缀 `analysis_report_*`，Alembic `f1_0017`（down=`f1_0016`）。
默认工程 migrate 仍锁 `f1_0014`。专属入口 `infra/f1/analysis-reports/migrate.py` 才请求 `f1_0017`。

## 产品权限（两类）

| 产品角色 | 会话推导 | 禁止 |
| --- | --- | --- |
| `provider_admin` | 当前企业 membership 的 `tenant.role` ∈ {`super_admin`,`enterprise_admin`} | 一次请求只操作一个已授权 `client_account_id`（本租户 `crm_account` **且** active audience binding）；realm-wide `tenant.roles` 永不补权 |
| `client_user` | 已认证且当前 membership 不是上表 | 不得提交 `client_account_id` / `tenant_id` / `enterprise_id` / `knowledge_scope_id`；只读 active binding 对应 provider/client 的 published 且 `artifact_ready` 版本，无绑定/撤销/他租户统一 404 或空列表 |

`GET /api/v1/session/access` 是唯一身份面。客户身份只从 Bearer + 既有成员关系推导。HTTP 合同字段不变，响应不返回 binding/provider/client ID。

内部 audience：`f1.analysis_report_client_audience` 把 provider `enterprise_id`、其 `crm_account`、客户 `audience_enterprise_id` 显式绑在一起。只允许 bootstrap/受控 fixture 写入；`f1_api` 只读；禁止按 UUID 相等自动插入。无绑定即拒绝。service 与 RLS 同时要求当前管理员 membership 或 active binding。

客户 RLS 无自引用、无 report↔version 环：报告行用本地 `client_visible`（发布/撤回维护）加上 active binding；版本/章节/引用只读 published 行上的 `client_account_id` 指针并回查 binding 表。binding 仍是最终授权条件。撤销后客户立即不可见；provider 可读历史并 withdraw，不得 create/generate/submit/approve/publish。

复合/延迟 FK：`current_version` 必须属于本 report；job/audit 的 version 必须属于同一 report。

## 模板

固定模板 `enterprise-ehs-material-analysis-v1`，标题「企业安环资料分析报告」。七章：资料范围、现状摘要、主要发现、风险与缺口、整改建议、引用证据、使用边界。

## 来源冻结

生成前只收：当前 provider `service_provider` scope **加上** 该唯一 client scope。每条必须 current、released、scan-clean、preview-ready、indexed。空客户不回退。跨租户/跨客户/stale/revoked/deleted/unreleased 在 generator 调用前拒绝。

指纹：tenant + client_account + template + 有序 source version/hash。换指纹且复用 `request_id` → 409。

## 状态机（版本）

`queued → generating → draft → review_pending → changes_requested|approved → published → superseded|withdrawn`

已发布版本不可原地改。修订生成新版本；发布时旧 published 变 superseded。撤回 published → withdrawn。

## 生成器

`ReportGeneratorPort`。本轮只实现 deterministic fake。双开关 `F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1` 且 `F1_LOCAL_ENGINEERING=1`，默认 fail-closed。不读任何 key。每条结论必须有合法 document-version/page citation；缺引用或 schema 不合格则整份失败。

## API 暴露

只返回逻辑 `report_id` / `version_id` / `document_version_id` / `page_number` / `citation_id`。禁止 dataset/chunk/scope/lease/物理存储 ID。
