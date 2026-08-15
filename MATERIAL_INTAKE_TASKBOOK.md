# MATERIAL INTAKE 任务书

> **历史范围说明：** 本任务书冻结的是 `codex/material-intake` 当轮完成门（`f1_0014` / 35 表）。2026-08-13 起源码唯一 head 为 `f1_0015`；现役状态与物理 RAG 边界以 [PROJECT_STATUS.md](./PROJECT_STATUS.md) 为准。

## 目标

把“选择材料到保存业务草稿”收成一个可用垂直切片：一次选择最多 10 份材料，逐份进入 P3 隔离并自动触发扫描/安全预览；PDF 生成逐页 OCR/表格/双栏提示、材料类型建议和带页码证据的字段候选。材料类型和知识归属分别由人决定；只有环保服务公司共享范围内、人工归为政策的材料，才能进入 P5 政策来源与版本草稿确认。

优先级：安全边界与数据正确 > 人工确认可追溯 > 录入速度 > 解析覆盖率。

## 拍板

- 第一版批量上传复用单文件受控 API，由浏览器维护逐项队列；每项独立幂等、独立成功或失败，最多 10 份、并发 2。
- 上传成功后服务端立即自动调用现有 P3 处理器；不自动 release。请求中断后材料仍留在隔离区，可用现有 process/retry 恢复，不宣称无人值守队列。
- 当前只用已锁定的 pypdf 规则生成法规字段和报告标题/日期/摘要候选；扫描型 PDF 只标 `OCR_REQUIRED`，低置信字段留空。
- confidence 是未校准的机器线索，不是准确率；每个非空候选至少有一个页码证据。
- 每份材料上传时先由人选择“政策／报告／待分类”，默认“待分类”；机器只给 `policy / report / unknown` 建议，不得自动覆盖人工分类。
- 材料类型与知识归属是两个独立维度。第一版知识归属只有 `service_provider / client`：政策库入口固定为环保服务公司共享，CRM 客户详情入口固定为当前客户；机器不得建议或修改归属。
- `enterprise_id` 继续作为安全工作区／租户边界，`crm_account` 是工作区内的业务客户。现有材料保守回填为 `service_provider`，不得根据文件名或正文猜客户。
- 客户材料即使被人工归为政策，也不能创建环保服务公司的 P5 政策草稿。未来新增厂区或服务任务维度时扩展 typed scope，不把供应商 dataset ID 或通用 target UUID 暴露给产品 API。
- 报告和待分类材料不出现政策草稿动作；人工可在分析页修正分类。报告整理入口另行建设，本轮不自动创建 P4 报告。
- pdf-inspector 只提供默认关闭的供应商中立 shadow 合同；不安装 0.2.6，不进入 API 进程，不影响 P3 主链。
- 只有文档已 `ready + released + clean + preview ready`，且服务端给出 `confirm_policy_draft`，才能人工确认；source + version + analysis + audit 单事务提交。

## 文件地界

允许新增 `f1_0011_material_intake.py`、后续线性 `f1_0012_material_routing.py`、`f1_0013_material_knowledge_scopes.py`、`f1_0014_low_level_material_scope.py`、`features/material_intake/**`、P3/P5 材料录入前后端组件、本任务三份文档和窄合成主链验证器 `infra/f1/local_material_verify.py`；允许修改 `models.py`、P3/P4/P5 router/service/contracts、P3/P4/P5 types/API、P3文档库/详情、CRM客户详情、政策库、App 路由，以及仅用于挂载验证器、把现役 head/35表目录和旧31/34表备份恢复兼容到当前 head 的本地运维脚本、Compose 与既有合同断言。更早 migration、requirements/lock、F1.1.1 证据只读。

不新增 OCR、LLM、联网法规抓取、RAG/索引、自动 release、自动审核/发布；不把正文、文件名、对象 key、凭据写入日志或 audit。

## 数据与 API

- `material_analysis`：绑定企业、document_version、source SHA 和分析版本；保存 profile、状态、确认结果与幂等摘要。
- `material_page_classification`：逐页 text/scanned/mixed、OCR required、table/two-column flags、字符数和未校准置信度。
- `material_field_candidate`：法规字段与报告标题/日期/摘要候选、值、页码、短证据、confidence ppm、producer；append-only。
- `material_knowledge_scope`：工作区内供应商中立的知识命名空间；第一版只允许一个公司共享 scope，或一个与 CRM 客户复合外键绑定的 client scope。
- `document_record.knowledge_scope_id`：每份逻辑文档只能绑定一个权威 scope；上传时由入口锁定，任一版本 release 后禁止改归属。
- `GET /v1/ingestion/versions/{id}/material-intake`：供应商中立分析快照与 allowed_actions。
- `PATCH /v1/ingestion/material-analyses/{id}/classification`：人工确认或修正政策／报告／待分类；只改去向，不创建业务记录。
- `PATCH /v1/ingestion/documents/{id}/knowledge-scope`：只在尚未 release 时人工修正公司／客户归属；更新、操作者和 audit 同事务。
- `POST /v1/policy-workflow/material-analyses/{id}/confirm`：人工最终 source/version 字段；单事务返回 source + draft version。

## 完成条件

1. PDF 上传无需再点“开始安全处理”；最多 10 份批量队列可逐项显示结果，分析详情能展示页级提示与候选，确认后只生成 draft、不自动审核或发布。
2. 唯一 Alembic head 为 `f1_0014`；35 张业务表继续 FORCE RLS，跨租户和无权客户材料（含底层原件与上传任务）不可见；客户、报告和待分类材料在服务与数据库两层都不能创建公司政策草稿；pdf-inspector 运行时仍 OFF，未新增依赖。

实现阶段只运行一个直接相关的最小检查；用户随后授权“正常验证”，因此另执行专属实库迁移和一份无客户数据的合成文本 PDF 垂直主链。仍未覆盖真实 OCR、真实客户材料、准确率评估、E2E 或发布验收。
