# MATERIAL INTAKE Progress

## 2026-08-12 服务公司／客户知识归属

- 材料类型与知识归属拆成两个独立维度：类型仍为政策／报告／待分类；归属新增 `service_provider`（环保服务公司）与 `client`（租户内 CRM 客户）。机器只建议材料类型，不决定归属。
- 新增线性迁移 `f1_0013 → f1_0012`：持久化一张知识域表，旧文档一律无推断回填到服务公司域；新租户或新客户在首个授权上传事务中按唯一键惰性创建知识域。
- 政策库入口固定服务公司域并默认政策；CRM 客户详情入口固定该客户域并默认待分类；通用受控文档入口固定服务公司域。客户材料即使人工分类为政策，也不会获得公司政策草稿动作。
- P3 document/version/preview 与材料分析、页分类、字段候选的 RLS 均显式传播知识域权限：服务公司域由 P3 管理角色访问；客户域仅管理员或该 CRM 客户负责人访问。材料发布后禁止修改归属。
- P5 在页面动作、服务事务和数据库 trigger 三层要求 `service_provider`，避免绕过前端把客户材料写入公司政策库。
- 本轮只建立供应商中立的知识域身份与入口，不创建或调用物理 RAGFlow dataset，不执行索引或检索。后续增加厂区／服务任务维度时扩展 scope 类型，不需要改动上传主链语义。
- 当前源码与实库 head 均为 `f1_0014`，业务表目录仍为 35；`f1_0014` 把知识域继续带到底层原件与受控上传任务，历史 31/34 表备份恢复入口保留兼容。
- 直接检查：`python3 -B -m unittest tests.test_p5_policy_workflow` → `Ran 9 tests / OK`（包含 `f1_0013` 及相关 Python 源码合同、前端 TypeScript `--noEmit`）。正常验证另见下方。
- 当前增量状态：`SMOKE_PASSED / NOT_PRODUCTION`；未运行浏览器、物理 RAG 或真实 Demo PDF 验证。

## 2026-08-12 人工优先材料分流

- 代表性 Demo PDF 验证暴露了真实产品缺口：报告材料也曾获得政策草稿动作。新增 `f1_0012 → f1_0011`，不新增表，在文档和分析记录上保存上传预分类、机器建议及最终人工分类。
- 批量上传每份材料默认“待分类”，人可先选政策或报告；分析页并列展示机器建议与人工结论，机器置信值继续明确标为未校准线索。
- `plant_admin / enterprise_admin / super_admin` 可人工修正去向；只有 `super_admin / enterprise_admin` 能确认政策草稿。
- 报告和待分类材料不会获得 `confirm_policy_draft`。P5 服务及数据库 trigger 再次校验 `resolved_kind=policy`、人工来源、操作者和文档安全状态，绕过页面直调也不能创建政策草稿。
- 报告只保留候选与受控原件；本轮仍不创建 P4 报告，不自动审核、发布或解除隔离，不引入 LLM/OCR/pdf-inspector 运行时。
- 该阶段源码 head 为 `f1_0012`；此前 `f1_0011` 合成 PDF smoke 证据继续有效，但不等于后续知识归属迁移已实库验证。
- 直接检查：`python3 -B -m unittest tests.test_p5_policy_workflow`。首轮仅因新增测试把 PL/pgSQL 赋值符写成普通等号而失败；修正测试断言后复跑 `Ran 9 tests / OK`，包含 Python 源码编译与前端 TypeScript `--noEmit`。
- 当前增量状态：`TARGETED_TEST_PASSED / NOT_PRODUCTION`。

## 2026-08-12 实现收口

- 基线：`codex/engineering-closeout@69f6d41`，新分支 `codex/material-intake`。
- 顺序：唯一迁移/模型 → P3分析与自动处理 → P5原子确认 → 批量上传与确认UI → 一个最小检查。
- 最大风险：扫描件没有 OCR 引擎；本轮只诚实标记 `OCR_REQUIRED`。pdf-inspector 正式包有已知供应链风险，运行时继续关闭。
- 已落 `f1_0011`：分析、逐页分类、字段候选三表，均 FORCE RLS；确认 trigger 绑定当前操作者、原文档 SHA、released/clean/ready 状态与新建 draft。
- P3 上传完成后自动进入现有 ClamAV + 安全预览处理；PDF 由 pypdf 生成文本/扫描、OCR 路由、表格/双栏及法规/报告候选，分析失败不反向污染 P3 ready 状态。
- P5 人工确认在一个事务内创建政策来源、版本 draft、analysis confirmed 和三条 audit；重复确认按分析、幂等键和 payload 摘要核对。
- 前端文档库支持最多 10 份、并发 2 的逐项队列；详情展示页码证据与未校准置信线索，并提供人工确认页。报告候选只展示，本轮不自动创建 P4 报告。
- `pdf-inspector` 仅新增默认关闭的供应商中立 capability seam；未安装、未 import、未进入 API/worker。
- 唯一直接检查：`python3 -B -m unittest tests.test_p5_policy_workflow` → `Ran 9 tests / OK`（包含 Python 源码合同和前端 TypeScript `--noEmit`）。
- 状态：`TARGETED_TEST_PASSED / NOT_PRODUCTION`。

## 2026-08-12 正常验证

- `./scripts/localctl migrate` 从实库 `f1_0011` 依次执行 `f1_0012 → f1_0013`，最终退出码 0，固定结果 `LOCAL_MIGRATE_OK`。
- 第一次迁移如实失败：旧文档回填受既有 `FORCE RLS` 遮蔽。该次 Alembic 事务整体回滚，没有留下半完成 schema 或半回填数据；修为只在迁移验证过的 bootstrap session 内对这段回填执行有界 `RESET ROLE`，随后完整重跑通过。
- 提交前权限审计又发现底层 `document/upload_task` 仍可能沿用租户级读取；新增 `f1_0014` 将 scope 带到底层原件，并对受控上传任务增加仅面向 API 的 restrictive policy。`f1_0013 → f1_0014` 实库迁移重跑输出 `LOCAL_MIGRATE_OK`。
- `./scripts/localctl material-verify` 只使用专属 PostgreSQL、MinIO、ClamAV；将同一份无客户数据的确定性合成文本 PDF 分别上传到服务公司域和一个 CRM 客户域，通过进程内 ASGI HTTP/API 路由走完扫描、安全预览、分析和释放。
- 最终固定结果 `LOCAL_MATERIAL_VERIFY_OK`：version=2、clean=2、preview=2、released object=2、analysis=2、page=2、未校准 candidate=8、knowledge scope=2（`service_provider=1`、`client=1`）；客户负责人可直接读取其底层原件与任务 2 行，同租户非负责人和跨租户均为 0。
- 服务公司材料创建 policy draft=1、自动 publication=0；客户材料由客户负责人可见=1、同租户非负责人可见=0、跨租户可见=0，且客户材料进入公司政策库在 API 与数据库 trigger 两层各拒绝 1 次。
- 随机 scratch 数据库、临时对象和随机 MinIO 桶残留均为 0；`./scripts/localctl stop` 固定结果 `LOCAL_STOPPED`。
- 状态为 `SMOKE_PASSED / NOT_PRODUCTION`；这不是浏览器验收、发布验收、准确率证明或生产验证。

## 尚未验证

- 未执行黑客松 Demo 中的真实材料 PDF、批量浏览器流程、OCR、备份恢复实跑或候选准确率评估；仅一份确定性合成文本 PDF 在两个知识域各上传一次并通过。
- 扫描型 PDF 仍只标 `OCR_REQUIRED`，不执行 OCR；confidence 仍是未校准线索。
- 未部署，也未恢复 F1.1.1 发布验收。
- 新的人工分流与公司／客户知识归属尚未跑浏览器或代表性 Demo PDF 回归。
- 尚未创建物理 RAG 库、执行索引或检索；当前知识域只是受 RLS 保护的产品归属与稳定 namespace 身份。
