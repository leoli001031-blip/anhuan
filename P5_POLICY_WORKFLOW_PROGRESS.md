# P5 POLICY WORKFLOW Progress

> **现役摘要（2026-08-11）：** `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`，且继续受 `NOT_LEGAL_ADVICE / PROFESSIONAL_JUDGMENT_REQUIRED` 限制。下文保留启动时状态与过程；总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 阶段启动

- 用户授权P4后严格串行进入P5；P6-P8继续排队，P8后停止。
- P4已收口为`P4_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`，F1.1.1保持暂停且不恢复验收。
- 已创建P5独立TASKBOOK/PROGRESS/BLOCKED；唯一迁移固定为`f1_0008_policy_workflow`、down revision `f1_0007`。
- 当前状态：`NOT_TESTED`；未运行数据库、服务、前端build或外部请求。

## 启动时计划（已完成）

1. 落`f1_0008`和ORM：source/version/review event/impact candidate/impact task、FORCE RLS及不可变/状态守卫。
2. 落P5后端feature/router：来源、版本候选、审核发布、影响任务、结构化检索。
3. 落P5前端feature：法规库、来源详情、版本审核、影响工作台。
4. 主agent接API main和App/Layout，执行一次P5最小检查并更新阶段标签。

## 2026-08-10 P5 功能收口

- 已新增唯一线性迁移`f1_0008_policy_workflow → f1_0007`与同步ORM，覆盖source/version/review event/impact candidate/impact task五表、企业复合FK、FORCE RLS及非破坏downgrade门。
- 版本内容自创建后不可变；状态机为`draft|rejected → in_review → approved → published`及`in_review → rejected`，同source旧published在新版本发布时转superseded。
- 数据库守卫把submit/approve/reject/publish/supersede绑定当前actor和精确角色，禁止自审；延迟约束要求同事务出现匹配的新review event，事件时间由服务端覆盖且append-only。
- 后端已挂载`/api/v1/policy-workflow`共17条operation，覆盖来源/版本、审核发布、影响/任务和结构化本地搜索；所有写入audit同session、单次commit。
- 前端已接入`/policies`、来源详情、版本审核和`/policy-impact`，按钮只看`allowed_actions`，企业切换取消旧请求并清空数据；无正文、抓取、外链、下载或专业判断动作。
- 本阶段唯一一次定向检查：`Ran 8 tests / OK`，包含Python源码编译、TypeScript `--noEmit`、线性迁移、状态/角色守卫、17条API和四页路由合同。
- 未运行数据库迁移、HTTP、真实页面、Docker、外部来源或发布验收。最终标签：
  - `P5_COMPLETE_NOT_RELEASE_VERIFIED`
  - `TARGETED_TEST_PASSED`
  - `CANDIDATE_ONLY`
  - `INTERNAL_REVIEW_ONLY`
  - `NOT_LEGAL_ADVICE`
  - `PROFESSIONAL_JUDGMENT_REQUIRED`
  - `NOT_PRODUCTION`

## 阶段交接

- P5源码实现结束；下一阶段严格串行进入P6自动化质量与合成Oracle。
- P5的数据库/HTTP/页面运行验证留到用户以后明确授权的验证轮，不在P6扩大。

## 2026-08-11 正常验证轮

- P3-P8联合定向回归中P5 8/8通过。
- UUID随机PostgreSQL/API/RLS smoke真实完成source→version→submit→独立auditor approve→publish→impact task，并验证B租户source/version均404、审计与清理全0。
- 当前标签更新为`P5_COMPLETE_NOT_RELEASE_VERIFIED + SMOKE_PASSED`；来源仍为合成内部数据，`NOT_LEGAL_ADVICE / PROFESSIONAL_JUDGMENT_REQUIRED / NOT_PRODUCTION`不变。
