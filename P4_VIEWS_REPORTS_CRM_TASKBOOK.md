# P4 VIEWS / REPORTS / CRM 任务书

> **已完成的历史执行合同。** P4 当前为 `P4_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文保留启动基线。

## 状态与边界

- P3实现已收口为`P3_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`；P4在同一未提交工作树继续开发，不回退P2/P3改动。
- 唯一线性迁移为`f1_0007_business_views_reports_crm`，`down_revision=f1_0006`。`f1_0001`至`f1_0006`只读，不允许第二Alembic head。
- P4只做角色驾驶舱、业务报告不可变快照/版本/元数据artifact、内部人工CRM；不做正式报告签发、外部发布、自动获客、短信邮件微信、合同回款、真实客户导入或生产部署。
- 页面固定提示：`BUSINESS_SNAPSHOT_ONLY / NOT_SIGNED / NOT_PUBLISHED / NOT_PRODUCTION`。
- 不恢复F1.1.1 formal/reverse/SBOM/clean/M4；不commit、不push、不部署、不删除共享数据。

## 产品闭环

驾驶舱发现待办 → 管理员建立内部客户档案 → 登记联系人和人工跟进 → 从服务任务建立报告 → 捕获不可变业务快照版本 → 查看来源计数和canonical artifact元数据 → 回到驾驶舱继续处置。

## Task 0：合同与并行地界

- 复用P2的`service_case/service_assignment/site_visit/finding/corrective_action/finding_review/business_timeline/in_app_notification`和P3的受控文档状态，不复制业务事实。
- 主agent单写任务文档、`f1_0007`、`models.py`、API main、App/Layout和所有既有共享文件。
- Backend子任务只新增`src/platform_foundation/f1/features/p4/**`和`src/platform_foundation/f1/api/routers/p4_views_reports.py`。
- Frontend子任务只新增`src/web/src/features/p4/**`；主agent最后一次性接App/Layout。
- 所有新表`ENABLE + FORCE RLS`，PUBLIC无表权；所有业务外键使用`enterprise_id`复合键；跨租户详情统一404，集合零行。
- 关键写操作与`audit_log`同事务；报告版本如关联服务任务，同时追加body-free timeline event。

## Wave 1：角色驾驶舱

- `GET /api/v1/views-reports/dashboard`返回`view/as_of/metrics/queues/allowed_actions`。
- view固定为`admin/consultant/partner/enterprise`，由后端基于当前企业membership和明确assignment决定，前端不猜角色。
- 指标覆盖进行中服务、未来现场服务、开放/逾期finding、待复核、受控文档ready/blocked、报告数、CRM待跟进。
- 队列每类最多10项，只返回当前RLS会话可见数据和业务ID，不跨企业汇总。
- 页面`/dashboard`包含KPI、待办卡、角色标签、加载/错误/空状态和基础窄屏布局。

## Wave 2：内部人工CRM

### 数据

- `crm_account`：内部客户/线索档案，字段含display_name、stage(`lead/active/dormant/closed`)、owner_user_id、industry_note、region_note、next_follow_up_at、创建/更新时间。
- `crm_contact`：account下联系人，字段含display_name、role_title、email、phone、status(`active/inactive`)；仅授权页面显示，不写日志/audit/timeline/localStorage。
- `crm_follow_up`：append-only人工记录，channel(`onsite/meeting/phone/internal_note`)、summary、next_action、next_due_at、occurred_at、actor；禁止UPDATE/DELETE。
- CRM仅允许内部合成/fixture数据；真实客户数据不开闸。

### API与页面

- `GET/POST /api/v1/views-reports/crm/accounts`
- `GET/PATCH /api/v1/views-reports/crm/accounts/{account_id}`
- `POST /api/v1/views-reports/crm/accounts/{account_id}/contacts`
- `PATCH /api/v1/views-reports/crm/contacts/{contact_id}`
- `POST /api/v1/views-reports/crm/accounts/{account_id}/follow-ups`
- 页面：`/crm`与`/crm/:accountId`，联系人和跟进在详情页抽屉/弹窗完成，不新增外部通讯动作。
- 管理员可创建/管理；owner可查看、编辑和追加跟进；partner只有明确成为owner时可见单条，不获得企业CRM总盘。

## Wave 3：业务报告快照与版本

### 数据

- `business_report`：enterprise、service_case、title、status(`active/archived`)、current_version_no、creator和时间。
- `business_report_version`：递增version_number、lifecycle(`current/superseded/void`)、change_note、canonical_snapshot JSONB、snapshot_sha256、snapshot_size_bytes、source_counts、creator和captured_at。
- `business_report_artifact`：每版本一条`canonical_json/database_snapshot/application/json/ready`元数据，SHA和size必须等于版本快照；不保存object URL、文件路径、PDF或HTML。
- canonical snapshot插入后不可修改；新版本只允许旧current变superseded，再插入新current。存在P4数据时downgrade必须在任何DROP前拒绝。

### 快照与事务

- 快照只捕获当前RLS可见的服务任务、分配、现场、finding/整改/复核、时间线聚合及可选已`ready+released+clean+preview ready`的受控文档ID/SHA/version；不复制文档正文、文件名、object key或生成专业结论。
- 在单事务内锁report、读取稳定排序来源、计算canonical JSON SHA/size、supersede旧版、插新版本和artifact、更新current_version_no、写audit及body-free timeline；失败整笔回滚。

### API与页面

- `GET/POST /api/v1/views-reports/reports`
- `GET /api/v1/views-reports/reports/{report_id}`
- `POST /api/v1/views-reports/reports/{report_id}/versions`
- `GET /api/v1/views-reports/report-versions/{version_id}`
- `POST /api/v1/views-reports/reports/{report_id}/archive`
- 页面：`/reports`、`/reports/:reportId`、`/reports/:reportId/versions/:versionId`，展示版本表、快照摘要、来源计数和artifact元数据，不出现签发/发布/PDF下载按钮。
- 管理员可创建/版本/归档；关联服务任务的accepted执行者可只读，accepted consultant可生成新版本；未授权统一404。

## Wave 4：集中接缝与可见体验

- Dashboard、CRM、Report按钮完全由服务端`allowed_actions`控制。
- 企业切换时取消旧请求并清空旧企业数据；集合有Spin/Alert/Empty，桌面表格与窄屏卡片均可操作。
- 把P4三组路由一次性接入App/Layout；不替换P2工作台根入口，`/dashboard`作为独立经营驾驶舱。
- 仅修无法启动/编译、主链阻断、数据损坏、明确跨租户越权或migration head冲突；其余登记BLOCKED后停止扩项。

## 验证与状态

- P4最多运行一个预计60秒内的直接相关最小检查；不跑全仓、E2E、coverage、benchmark、生产build或F1.1.1验收。
- 最小检查覆盖单一`f1_0007→f1_0006`、报告版本/SHA一致、CRM append-only、allowed actions及关键路由合同。
- 未运行则标`NOT_TESTED`；通过仅标`TARGETED_TEST_PASSED`。阶段完成使用`P4_COMPLETE_NOT_RELEASE_VERIFIED`，禁止`RELEASE_VERIFIED`。
