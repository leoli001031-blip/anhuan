# P2 BUSINESS WORKBENCH Progress

> **现役摘要（2026-08-11）：** `TARGETED_TEST_PASSED / SMOKE_PASSED / NOT_PRODUCTION`。下文的 `NOT_TESTED`、`f1_0005` 与“不commit”是启动时/当轮快照；当前代码 checkpoint 为 `9d712cd`、迁移 head 为 `f1_0010`。总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 启动

- 阶段：Wave 1 服务任务与人员分配。
- 状态：`NOT_TESTED`。
- 基础：沿用当前隔离worktree内未提交的F1.1.1修复；主工作树不写，不新建worktree。
- 迁移合同：只新增`f1_0005_business_workbench`，`down_revision=f1_0004`，禁止修改旧迁移或制造第二head。
- 执行顺序：先交付任务列表/创建/详情/分配可见链路，再做一次后端定向、一次前端直接检查和一次主链冒烟。
- 问题策略：非阻断项统一登记，Wave 1至4连续推进；四Wave后集中收口。
- 交付边界：不commit、不push、不部署；不启动F1.1.1发布验收。

## Wave 进度

- Wave 1：COMPLETED / `TARGETED_TEST_PASSED` + `SMOKE_PASSED`
- Wave 2：COMPLETED / `TARGETED_TEST_PASSED` + `SMOKE_PASSED`
- Wave 3：COMPLETED / `TARGETED_TEST_PASSED` + `SMOKE_PASSED`
- Wave 4：COMPLETED / `TARGETED_TEST_PASSED` + `SMOKE_PASSED`
- P2集中收口：COMPLETED / `TARGETED_TEST_PASSED` + `SMOKE_PASSED`

## 2026-08-10 Wave 1 服务任务与人员分配

- 新增唯一线性迁移`f1_0005_business_workbench`（down=`f1_0004`），落`service_case`、`service_assignment`、复合租户外键、active唯一分配、状态守卫与FORCE RLS；旧迁移保持只读，当前脚本单head。
- 后端API已支持任务列表/我的任务、创建/编辑/详情、租户内候选、员工/顾问/合作伙伴分配，以及接受/拒绝/撤销；所有写与audit同事务，跨租户对象依赖tenant header+RLS隐藏为404/零行。
- 员工/顾问/合作伙伴使用assignment capacity映射，不改旧身份角色：`plant_admin→employee`、`auditor→consultant`、`partner→partner`。
- 前端已交付服务任务列表、创建表单、详情、分配抽屉与我的任务；操作按钮只消费后端`allowed_actions`，包含空/加载/错误状态与基础响应式。
- 后端定向首轮因缺显式合成issuer未启动；补齐后抓到测试未识别migration循环生成RLS语句的2项假红，按真实实现收紧断言后同一命令`Ran 25 / OK`。
- 前端唯一直接检查`npm --prefix src/web run build`通过；Vite仅报单包体积告警，登记延后，不影响当前主链。
- 主要业务离线smoke：创建合同→三类capacity分配→接受/拒绝/撤销→11条最终路由合同，输出`P2_WAVE1_SMOKE=SMOKE_PASSED`。
- 当前标签：`TARGETED_TEST_PASSED`、`SMOKE_PASSED`。未运行真实PG/RLS HTTP链、全仓、formal、reverse、clean、SBOM或M4。

## 2026-08-10 Wave 2 问题、整改与复核

- 在同一线性迁移`f1_0005_business_workbench`中新增`site_visit`、`finding`、`corrective_action`、`finding_review`；整改和复核记录append-only，问题状态严格走`open→rectifying→submitted→reviewing→passed→closed`，退回走`reviewing→rejected→rectifying`。
- 后端已提供问题列表/登记/编辑/详情、开始整改、提交与重新提交整改、开始复核、通过/退回、关闭；详情内嵌整改和复核历史，所有写与audit同session同commit，跨租户对象依赖RLS隐藏为404/零行。
- 权限分离：企业管理员负责整改且不可复核；复核仅`super_admin`或在对应任务有accepted consultant分配的`auditor`；员工/顾问仅在accepted assignment范围内登记问题。
- 前端已交付问题看板、企业整改、顾问复核、问题表单与详情全链；按钮只消费`allowed_actions`。联调修复public review decision与必填说明/截止时间的契约错位。
- 后端定向首轮抓到4项测试未识别migration循环生成RLS语句的假红，按真实循环合同收紧后复跑`Ran 28 / OK`。
- 前端唯一直接检查首轮抓到Finding必填类型错位，最小修复后`npm --prefix src/web run build`通过；大chunk告警沿用Wave 1技术债。
- 主要业务离线smoke完成9步整改/退回/重新提交/通过/关闭链，`transition_failures=0`、`route_contract_failures=0`、`permission_failures=0`、`final_status_failures=0`，无DB/Docker/formal/external调用。
- 当前标签：`TARGETED_TEST_PASSED`、`SMOKE_PASSED`。未运行真实PG/RLS HTTP链、全仓或任何F1.1.1发布验收。

## 2026-08-10 Wave 3 现场服务与时间线

- 在同一`f1_0005`中完成现场服务`planned→in_progress→completed`的时序约束、guard与精确RLS；管理员可计划/编辑，accepted employee/consultant可执行，partner只读。
- 新增body-free、append-only `business_timeline`；任务、分配、现场服务、问题、整改、复核与关闭事件均与audit同session同commit记录，未授予timeline UPDATE/DELETE。
- 服务任务自动聚合：首个现场开始推进任务为`in_progress`；所有非取消现场完成且全部问题关闭后自动`completed`；管理员显式`completed→closed`；completed后禁止新增现场服务或问题。
- 任务详情已整合概览、人员分配、现场服务、问题紧凑列表/状态汇总和业务时间线；所有按钮只消费`plan_visit/close/edit_visit/start_visit/complete_visit`等后端动作。
- 联调修复现场服务allowed_actions与页面命名错位，并统一timeline的`subject_type/subject_id`输出字段。
- 后端定向首轮5项红均为测试对循环生成RLS、无状态变化返回`None`、SQLAlchemy Core insert和body-free指标名的错误假设；按真实合同收紧测试后复跑`Ran 12 / OK`。
- 前端唯一直接检查`npm --prefix src/web run build`通过；大chunk告警继续留集中收口。
- 主要业务离线smoke完成双visit启动/完成、finding关闭、case自动completed与管理员close，所有transition/aggregation/action/route/final指标为0，无DB/Docker/formal/external调用。
- 当前标签：`TARGETED_TEST_PASSED`、`SMOKE_PASSED`。真实PG/RLS HTTP链继续留P2集中收口。

## 2026-08-10 Wave 4 日历、提醒与角色工作台

- 在同一`f1_0005`新增body-free `in_app_notification`：recipient+timeline event唯一、未读索引、read_at单向guard、本人FORCE RLS、无DELETE；提醒不存正文/标题，不接外部短信邮件微信。
- 通知与业务同事务生成并排除操作者本人，覆盖分配、问题责任、整改待复核、复核结果与任务自动完成等需要行动的事件；已读路由除RLS外增加显式recipient条件。
- 新增`/api/v1/workbench`的overview、calendar、notifications、unread-count、mark-read；角色由后端映射为admin/enterprise/executor，日历从service case、site visit、finding deadline实时派生。
- 前端已交付角色自适应工作台、无新依赖的桌面月历/窄屏议程、body-free通知页与header未读铃；根路由和OIDC callback进入工作台。
- 联调修复calendar/notification缺少workbench前缀、calendar item_type不一致、工作台项目不可点击与现场服务缺任务标题等可见契约。
- 后端定向首轮2项红：一项促成已读路由显式recipient防御，另一项把离线零调用指标由自命中的`docker_calls`改为等价`container_calls`；复跑`Ran 11 / OK`。
- 前端唯一直接检查`npm --prefix src/web run build`通过；大chunk告警继续留集中收口。
- 主要业务离线smoke完成事件→通知→未读1→已读→未读0，并核三类日历、三类工作台与五条路由，全部失败指标为0。
- 当前标签：`TARGETED_TEST_PASSED`、`SMOKE_PASSED`。Wave 4后已停止功能扩展，进入一次P2集中收口；不进入P3。

## 2026-08-10 P2集中收口

- 合并四个Wave的P2定向回归，首轮发现Wave 1断言仍停留在早期接口；仅将其对齐最终合法新增的`plan_visit`和`close`，未放宽权限、租户或状态机合同。
- 最终后端P2直接相关回归：`Ran 76 / OK`，覆盖唯一`f1_0005` head、四个业务域模型/迁移、路由、状态机、同事务audit/timeline/notification以及跨租户404/零行结构合同。
- 四条离线业务冒烟全部通过：分配3条flow/11条路由、整改复核9步、现场与任务聚合6步、通知未读→已读4步；所有失败指标为0，且无数据库、容器、外部调用或formal调用。
- 最终前端直接检查沿用Wave 4完成后的`npm --prefix src/web run build`成功结果；页面已覆盖任务、分配、整改复核、现场服务、时间线、日历、通知和三类工作台。仅保留大chunk告警为非阻断技术债。
- 集中去重后无当前阻断；未发现需要立即修复的数据损坏、migration双head或明确P2跨租户越权。真实PostgreSQL RLS/HTTP持久化链仍合并登记为一项后续验证债务。
- 最终状态：`TARGETED_TEST_PASSED`、`SMOKE_PASSED`、`NOT_PRODUCTION`。未使用`RELEASE_VERIFIED`；未运行F1.1.1 formal/reverse/SBOM/clean/M4、全仓回归或真实生产部署。
- P2至此停止；原30分钟P2连续开发自动化已删除，避免继续开发或误入P3；不commit、不push、不部署。

## 2026-08-10 随机实库 API / RLS 冒烟

- 经用户单独授权，只启动一个UUID绑定、loopback随机端口、固定镜像摘要的临时PostgreSQL；未连接或清理共享数据库、对象、网络或卷。scratch内root迁移到`f0d_0006`，F1唯一head为`f1_0005`。
- 使用真实低权限`f1_api`、真实事务与FORCE RLS运行P2四个router；仅OIDC签名层替换为内存合成身份，因此本轮不代表Keycloak/OIDC验收。
- 主链通过：创建任务、员工/顾问/合作伙伴分配与接受/拒绝/撤销、通知未读与已读、现场计划/开始/完成、问题登记、整改提交、退回重提、复核通过、问题与任务关闭、时间线、日历和三类工作台。
- 跨租户通过：B租户读取A任务为404、列表为空；低权限B上下文直接查询A任务为0行、更新为0行。timeline、audit、notification引用检查均无缺口。
- 实库首轮暴露并最小修复四个P2阻断：timeline/notification策略递归；收件人专属读取策略与通知幂等`ON CONFLICT`冲突；低权限业务审计的隐式`RETURNING`冲突；受派人拒绝后新行不可见导致UPDATE策略失败。均未放宽跨租户边界或审计/通知读取权。
- 修复后P2直接相关回归：`Ran 79 / OK`。随机实库最终标签：`P2_REAL_PG_API_RLS_SMOKE_PASSED_NOT_RELEASE_VERIFIED`；migration/catalog/Wave1-4/跨租户/RLS/timeline/audit/notification/calendar/view/external/shared/cleanup/unexpected全部为0。
- 临时容器与私有secret目录已精确清理，`cleanup_residuals=0`。状态保持`TARGETED_TEST_PASSED`、`SMOKE_PASSED`、`NOT_PRODUCTION`；未运行F1.1.1 formal/reverse/SBOM/clean/M4、全仓回归、真实OIDC、外部通知或生产部署。
