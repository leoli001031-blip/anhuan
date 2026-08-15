# P6 AUTOMATED QUALITY Progress

> **阶段收口摘要（2026-08-11）：** `P6_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；仅证明合成 Oracle 工作流，不是 Gold 或真实准确率。下文保留启动时状态与过程；总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 阶段启动

- P5已完成并按严格串行进入P6；P7-P8继续排队，P8后停止。
- 唯一迁移固定`f1_0009_automated_quality → f1_0008`。
- 已冻结五表、有限JSON、确定性Oracle、分歧处置和四页合同；阶段状态`NOT_TESTED`。
- 未运行数据库、服务、前端build或外部调用。

## 启动时计划（已完成）

1. 落`f1_0009`与ORM，包含suite/scenario/run/result/disagreement及RLS/不可变守卫。
2. 落P6后端纯本地Oracle、run/result、分歧与dashboard。
3. 落P6前端质量驾驶舱、套件、run、分歧页面。
4. 主agent接main/App/Layout，执行一次P6最小检查并更新标签。

## 2026-08-10 数据层收口

- 已新增线性迁移`f1_0009_automated_quality → f1_0008`与五个ORM模型；未修改旧迁移，仍为单一head。
- 已落suite/scenario/run/result/disagreement的企业复合FK、FORCE RLS、有限JSON、不可变result与分歧人工处置守卫。
- 只读暗查发现并已闭合两项数据一致性接缝：result仅能写入同suite且处于running的run；run终结计数必须与实际不可变result逐项一致；disagreement仅能关联failed result且kind必须由scenario显式声明。
- 后端与前端仍在各自新增目录收口；当前尚未运行P6唯一直接检查，阶段标签保持`NOT_TESTED`。

## 2026-08-10 阶段完成

- 后端已完成有限合成JSON、六类确定性Oracle、suite/scenario、同步run/result、分歧处置与合成质量dashboard，共10个路由；无OCR/LLM/RAGFlow/provider或外部网络调用。
- 前端已完成质量驾驶舱、套件详情、run详情与分歧队列四页；所有动作只消费`allowed_actions`，企业切换会abort并清空旧租户数据。
- 主线已挂载`/api/v1/automated-quality`，并接入`/quality`、套件、run和分歧路由及“合成质量”菜单。
- 本阶段唯一直接检查：`env F1_KEYCLOAK_ISSUER_URL=http://127.0.0.1:31001/realms/anhuan PYTHONPATH=src /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest -v tests.test_p6_automated_quality`；结果`Ran 7 tests in 1.360s / OK`，含Python源码编译、TypeScript `--noEmit`、线性迁移/数据守卫、纯Oracle确定性、API和页面合同。
- 完成标签：`P6_COMPLETE_NOT_RELEASE_VERIFIED + TARGETED_TEST_PASSED`。
- 固定边界：`SYNTHETIC_ORACLE_ONLY / NON_GOLD / ACCURACY_NOT_EVALUATED / NO_EXTERNAL_MODEL_CALLS / NOT_PRODUCTION`。
- 未运行数据库迁移、服务、E2E、全仓、coverage、benchmark、生产build或发布验收；未commit、push或部署。

## 2026-08-11 正常验证轮

- P3-P8联合定向回归中P6 7/7通过。
- UUID随机PostgreSQL/API/RLS smoke真实完成suite→scenario→确定性failed run→disagreement→auditor acknowledged，验证B租户suite/run均404，且`external_calls=0`。
- 当前标签更新为`P6_COMPLETE_NOT_RELEASE_VERIFIED + SMOKE_PASSED`；只证明合成Oracle工作流，不代表真实OCR/问答准确率或Gold质量。
