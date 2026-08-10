# P7 LOCAL PRODUCTION REHEARSAL ONLY 任务书

> **已完成的历史执行合同。** P7 当前为 `P7_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文保留启动基线。

## 状态与边界

- P6已收口为`P6_COMPLETE_NOT_RELEASE_VERIFIED + TARGETED_TEST_PASSED`；P7严格串行开始。
- 唯一线性迁移为`f1_0010_local_rehearsal`，`down_revision=f1_0009`；此前迁移只读，禁止第二Alembic head。
- P7只做本地人工演练计划、检查清单、演练run、不可变检查结果、失败与回滚门；不执行部署、不连接生产、不配置域名/生产凭据/SLA、不触发真实通知。
- 固定边界：`LOCAL_REHEARSAL_ONLY / MANUAL_EXECUTION / NO_PRODUCTION_ACCESS / NO_DEPLOYMENT / NOT_PRODUCTION`。
- 不恢复F1.1.1发布验收；不跑全仓/E2E/coverage/benchmark/生产build；不commit、不push、不部署、不删除共享数据。

## 产品闭环

创建本地演练计划 → 编排检查项 → 启动演练run并冻结本次清单 → 人工逐项记录通过/失败/阻断及摘要 → 必需项全部通过才可完成 → 失败或阻断时明确要求回滚 → 关闭run并保留时间线。

## Task 0：合同与并行地界

- 主agent单写任务文档、`f1_0010`、`models.py`、API main、App/Layout和共享文件。
- Backend子任务只新增`src/platform_foundation/f1/features/p7/**`和`src/platform_foundation/f1/api/routers/p7_local_rehearsal.py`。
- Frontend子任务只新增`src/web/src/features/p7/**`；最后由主agent接App/Layout。
- 四张新表均企业复合FK、`ENABLE + FORCE RLS`、PUBLIC/worker无权；结果append-only，跨租户详情404/集合零行；不新增SECURITY DEFINER。
- 不存DSN、token、key、绝对路径、客户正文、日志正文、object URL；证据仅保存SHA256与固定reason code。

## Wave 1：演练计划与清单

- `rehearsal_plan`：name、status(`draft/active/archived`)、execution_mode固定`local_manual`、creator/time。
- `rehearsal_check`：plan、check_key、category(`service/dependency/backup/restore/security/rollback`)、label、sequence_no、required、enabled、creator/time。
- API：`GET/POST /plans`、`GET /plans/{id}`、`POST /plans/{id}/checks`、`PATCH /checks/{id}`。
- 页面：`/rehearsal`、`/rehearsal/plans/:planId`。

## Wave 2：本地演练run与冻结结果

- `rehearsal_run`：plan、status(`planned/running/passed/failed/cancelled`)、counts、rollback_required、creator/start/complete time。
- `rehearsal_check_result`：run、check、status(`pending/passed/failed/blocked`)、reason_code、evidence_sha256、recorded_by/time；每个run/check唯一。
- 启动run时同事务锁定active plan和enabled checks，并为本run生成全量pending结果；后续计划改动不改变该run集合。
- API：`POST /plans/{id}/runs`、`GET /runs/{id}`、`PATCH /runs/{id}/checks/{result_id}`、`POST /runs/{id}/complete|cancel`。
- 页面：`/rehearsal/runs/:runId`。

## Wave 3：完成门与回滚语义

- 必需检查全部passed且无failed/blocked才可把run置passed。
- 任一required failed/blocked则run只能failed，且`rollback_required=true`；人工确认结果不能改写历史记录，只能新开run复演。
- result首次从pending变terminal后不可更新/删除；run完成后不可新增或改结果。
- 每个写操作与body-free audit event同事务；不自动运行shell、Docker、数据库恢复、部署或生产动作。

## Wave 4：本地演练驾驶舱

- `GET /dashboard`返回计划/run/结果聚合、待执行计划与最近run；所有卡片标注“本地人工演练”。
- 页面按钮只看`allowed_actions`，企业切换abort/清空；含加载、错误、空态及响应式表格/卡片。
- 仅修启动/编译、主链阻断、数据损坏、明确跨租户越权、migration head冲突；其余登记BLOCKED。

## 验证与状态

- P7最多一个预计60秒内的直接相关检查，覆盖`f1_0010 → f1_0009`、状态/完成门、不可变result、API/页面合同。
- 未运行标`NOT_TESTED`；通过仅`TARGETED_TEST_PASSED`。完成标签`P7_COMPLETE_NOT_RELEASE_VERIFIED`，禁止`RELEASE_VERIFIED`或任何生产就绪声明。
