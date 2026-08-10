# P2 BUSINESS WORKBENCH 任务书

> **已完成的历史执行合同。** P2 当前为 `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文保留启动时边界，其中“不commit”已被后来三个用户授权的本地 checkpoint 覆盖，但仍无 push 或部署。

## 状态与边界

- F1.1.1固定为`F1_1_1_PAUSED_NOT_ACCEPTED`；P2不恢复formal、reverse、SBOM、clean rebuild、全仓回归或M4。
- 在当前隔离worktree增量开发，不commit、不push、不部署，不删除或迁移共享数据。
- 迁移唯一线性前进：`f1_0005_business_workbench`，`down_revision=f1_0004`。`f1_0001`至`f1_0004`只读，禁止第二Alembic head。
- P2不做文件上传/quarantine/恶意扫描、PDF/DOCX/XLSX预览、正式报告、法规审核发布、外部短信邮件微信、Gold/准确率、生产部署或正式小程序。

## 产品闭环

创建服务任务 → 分配员工/顾问/合作伙伴 → 开展现场服务 → 登记问题 → 企业提交整改 → 顾问退回或通过复核 → 关闭问题与服务 → 自动形成时间线、日历和站内提醒。

## 权限合同

- 平台管理员：创建服务、分配人员、查看授权范围内任务。
- 员工/顾问：只查看分配给自己的服务，登记现场记录和问题。
- 合作伙伴：只查看明确分配给自己的任务，不可重新分配。
- 企业管理员：只查看本企业任务并提交整改。
- 复核人员：只复核授权范围内整改。
- 未授权或跨租户对象统一404或零行；关键状态变化与timeline event同事务。
- 复用现有租户上下文与RLS模式，不新增复杂Definer体系。

## Wave 1：服务任务与人员分配

- 数据：`service_case`、`service_assignment`。
- 服务任务创建、编辑、列表、详情；员工/顾问/合作伙伴分配。
- 接受、拒绝、撤销分配；“我的任务”；管理员与执行人员角色可见性。
- 页面：任务列表、创建表单、详情页、分配抽屉；含基础空/加载/错误状态。
- 完成门：可见UI主链可操作，租户隔离明确；一个后端定向检查、一个前端直接检查、一次主链冒烟。

## Wave 2：问题、整改、复核

- 数据：`site_visit`、`finding`、`corrective_action`、`finding_review`。
- 严格主状态机：`open→rectifying→submitted→reviewing→passed→closed`；退回：`reviewing→rejected→rectifying`。
- 严重程度、责任人、截止时间；企业提交/重新提交；顾问通过/退回。
- 页面：问题整改看板、企业整改页、顾问复核页。

## Wave 3：现场服务与时间线

- 现场服务计划、开始、完成；服务任务状态自动聚合。
- append-only业务时间线覆盖创建、分配、现场执行、问题、整改、复核与关闭。
- 任务详情整合概览、人员、现场服务、问题整改、时间线。

## Wave 4：日历、提醒与角色工作台

- 服务日历、整改截止日、待复核事项。
- 站内提醒、未读计数、标记已读。
- 管理员、顾问/员工、企业三类工作台；基础响应式与空/加载/错误状态。
- Wave 4后停止，不自动进入P3；进入一次P2集中收口轮。

## 功能优先与集中收口

- Wave期间只立即修：无法启动/编译、当前主链阻断、数据损坏、明确P2跨租户越权、migration head冲突。
- 其余问题只在BLOCKED/技术债登记`reason / 影响面 / 建议修复点`，不启动审计支线、不做通用重构或性能优化。
- 四个Wave完成后集中去重：先阻断/安全/数据一致性，再跑P2直接相关回归；未经用户要求不恢复F1.1.1验收，不进入P3。

## 验证与状态标签

- 每Wave最多：一个后端P2定向检查、一个前端lint或目标build、一次主要业务冒烟。
- 禁止以全仓discover、F1.1.1 formal、双轮clean或完整生产构建替代P2检查。
- 状态仅：`NOT_TESTED`、`SMOKE_PASSED`、`TARGETED_TEST_PASSED`；P2不得使用`RELEASE_VERIFIED`。
