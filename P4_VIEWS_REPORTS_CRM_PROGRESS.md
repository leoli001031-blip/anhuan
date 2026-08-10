# P4 VIEWS / REPORTS / CRM Progress

> **现役摘要（2026-08-11）：** `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`。下文保留启动时状态与过程；总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 阶段启动

- 用户已授权P3后严格串行自动推进P4→P5→P6→P7→P8；P8后停止，不进入真实UAT、生产或正式小程序。
- P3实现已收口为`P3_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`；F1.1.1保持`PAUSED_NOT_ACCEPTED`且不恢复验收。
- 已创建P4独立TASKBOOK/PROGRESS/BLOCKED；唯一迁移固定为`f1_0007_business_views_reports_crm`、down revision `f1_0006`。
- 已完成P4后端数据/API/RLS与前端路由/页面合同的只读规划；当前开始Wave 1-3并行新增文件，shared migration/models/main/App/Layout由主agent单写。
- 当前状态：`NOT_TESTED`。未运行数据库、前端build、服务或外部调用。

## 启动时计划（已完成）

1. 落`f1_0007`和ORM：CRM三表、报告三表、复合租户FK、FORCE RLS和append-only/不可变守卫。
2. 落P4后端feature/router：角色驾驶舱、CRM、报告snapshot/version/artifact metadata。
3. 落P4前端feature：驾驶舱、CRM列表/详情、报告列表/版本详情。
4. 主agent接API main和App/Layout，做一次允许的P4最小检查并更新阶段标签。

## 2026-08-10 数据边界已落

- 已新增唯一线性迁移`f1_0007_business_views_reports_crm → f1_0006`，包含CRM account/contact/append-only follow-up与report/version/artifact六表。
- 六表均使用企业复合外键、`ENABLE + FORCE RLS`、PUBLIC/worker零表权；版本快照和artifact元数据由守卫保持不可变，artifact SHA/size必须与snapshot一致。
- 报告读权限覆盖管理员和明确accepted assignment；写版本只允许管理员或accepted consultant，非管理员不能修改报告标题或归档。
- downgrade会在任何DROP前检查P4数据；同一DDL事务临时取消owner的FORCE过滤，存在数据即回滚并拒绝。
- ORM元数据已同步六表、状态常量、复合外键及关键唯一/检查约束。
- 对迁移/RLS接缝完成一次只读subagent复核，原两项阻断已闭合；这不是运行验证，阶段仍为`NOT_TESTED`。

## 2026-08-10 P4 功能收口

- 后端已挂载`/api/v1/views-reports`共14个operation：角色驾驶舱1、内部CRM 7、报告快照/版本6。
- CRM已具备列表、创建、详情、编辑、联系人与append-only人工跟进；所有写入与audit同session、单次commit。
- 报告已具备创建、不可变canonical snapshot版本、artifact SHA/size一致元数据、查看和归档；报告写同时追加body-free timeline。
- 前端已接入`/dashboard`、`/crm`、`/crm/:accountId`、`/reports`、`/reports/:reportId`、`/reports/:reportId/versions/:versionId`，并加入三项导航；P2根工作台未替换。
- 页面由服务端`allowed_actions`控制，企业切换会取消旧请求并清空旧数据；不含下载、PDF/HTML生成、签发或发布动作。
- 本阶段唯一一次定向命令运行8项：7项通过（含TypeScript `--noEmit`、Python源码编译、迁移/RLS/路由/页面合同），1项因测试导入时缺`F1_KEYCLOAK_ISSUER_URL`而ERROR。已修正该测试前置但按单阶段一次检查预算未重跑。
- 未运行数据库迁移、HTTP、真实页面、Docker、全仓或发布验收。因此最终标签为：
  - `P4_COMPLETE_NOT_RELEASE_VERIFIED`
  - `NOT_TESTED`
  - `BUSINESS_SNAPSHOT_ONLY`
  - `NOT_SIGNED`
  - `NOT_PUBLISHED`
  - `NOT_PRODUCTION`

## 阶段交接

- P4源码实现结束；下一阶段严格串行进入P5法规来源、审核与影响工作流。
- P4测试环境错误与所有运行验证留到用户以后明确授权的集中验证轮，不在P5期间回头展开。

## 2026-08-11 正常验证轮

- 联合定向回归中P4全部8项通过，原issuer测试前置错误已闭合；包含TypeScript `--noEmit`、Python编译、迁移/RLS、路由与页面合同。
- UUID随机PostgreSQL/API/RLS smoke真实完成case锚点、CRM账户/联系人/append-only跟进、基础报告及不可变版本，并验证B租户详情404、直接RLS读写零行和审计存在。
- P4 smoke聚合`p4_failures=0`、`cross_tenant_api_leaks=0`、`rls_select_leaks=0`、`rls_write_leaks=0`、`audit_gaps=0`、`cleanup_residuals=0`。
- 当前标签更新为`P4_COMPLETE_NOT_RELEASE_VERIFIED + SMOKE_PASSED`；没有PDF/HTML正式报告、签发、发布或真实客户数据。
