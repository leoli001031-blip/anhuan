# P7 LOCAL PRODUCTION REHEARSAL ONLY Progress

> **阶段收口摘要（2026-08-11）：** `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；未执行真实恢复、故障切换、shell、部署或生产访问。下文保留启动时状态与过程；总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 阶段启动

- P6已完成并按严格串行进入P7；P8继续排队，P8后停止。
- 唯一迁移固定`f1_0010_local_rehearsal → f1_0009`。
- 已冻结四表、本地人工计划/清单/run/result、完成与回滚门、三页合同；阶段状态`NOT_TESTED`。
- 未运行数据库、服务、前端build、Docker、shell演练或生产动作。

## 启动时计划（已完成）

1. 落`f1_0010`与ORM，包含plan/check/run/result及RLS/不可变守卫。
2. 落P7后端人工演练状态机、完成门、dashboard。
3. 落P7前端演练驾驶舱、计划详情、run详情。
4. 主agent接main/App/Layout，执行一次P7最小检查并更新标签。

## 2026-08-10 数据层收口

- 已新增线性迁移`f1_0010_local_rehearsal → f1_0009`与四个ORM模型，未修改旧迁移。
- 已落plan/check/run/result企业复合FK、FORCE RLS、清单snapshot、pending→terminal一次性结果守卫、run完成门与失败回滚标记。
- optional或required检查只要出现failed/blocked都不能标passed；run必须终结为failed并置`rollback_required=true`，避免无法关闭的状态缝隙。
- 后端与前端仍在各自新增目录收口；当前尚未运行P7唯一直接检查，阶段标签保持`NOT_TESTED`。

## 2026-08-10 阶段完成

- 后端已完成计划、检查项、冻结run snapshot、人工结果、完成/取消与dashboard，共11个路由；代码不执行shell、Docker、恢复或部署动作。
- 前端已完成本地演练驾驶舱、计划详情、run详情三页，失败/阻断与`rollback_required`强可见；没有生产或部署按钮。
- 主线已挂载`/api/v1/local-rehearsal`，并接入`/rehearsal`、计划、run路由及“本地演练”菜单。
- 本阶段唯一直接检查运行7项：Python编译、TypeScript `--noEmit`、迁移/模型/状态门/页面接缝均通过6项；唯一失败来自测试把固定边界字符串`NO_DEPLOYMENT`误命中为可执行deploy代码。该测试已改为仅检查`import subprocess/subprocess./os.system/docker run/kubectl/ssh`精确执行模式，但按单阶段一次检查预算未重跑。
- 完成标签：`P7_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`；不是功能代码失败，也没有数据库/运行时证据。
- 固定边界：`LOCAL_REHEARSAL_ONLY / MANUAL_EXECUTION / NO_PRODUCTION_ACCESS / NO_DEPLOYMENT / NOT_PRODUCTION`。
- 未运行数据库迁移、服务、Docker演练、恢复、E2E、全仓、production build或发布验收；未commit、push或部署。

## 2026-08-11 正常验证轮

- P3-P8联合定向回归中P7 7/7通过。
- 首次UUID实库/API smoke在auditor记录检查结果时返回404；根因是并发安全所需`FOR UPDATE`锁run，却只有manager UPDATE RLS可见。
- 已新增窄`p7_rehearsal_run_operator_lock`策略：auditor仅可锁定`running` run，`WITH CHECK (false)`继续拒绝任何实际run UPDATE；保持run→result锁顺序。
- 修复后真实完成plan→check→run→auditor failed result→manager complete，终态`failed + rollback_required=true`；P4-P7 smoke所有聚合指标全0且无scratch残留。
- 当前标签更新为`P7_COMPLETE_NOT_RELEASE_VERIFIED + SMOKE_PASSED`；本轮没有执行shell、Docker恢复、故障切换或生产部署动作。
