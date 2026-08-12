# MATERIAL INTAKE 任务书

## 目标

把“选择材料到保存政策草稿”收成一个可用垂直切片：一次选择最多 10 份材料，逐份进入 P3 隔离并自动触发扫描/安全预览；PDF 生成文档类型、逐页 OCR/表格/双栏提示和带页码证据的字段候选；用户编辑确认后，P5 在同一事务创建政策来源与版本草稿。

优先级：安全边界与数据正确 > 人工确认可追溯 > 录入速度 > 解析覆盖率。

## 拍板

- 第一版批量上传复用单文件受控 API，由浏览器维护逐项队列；每项独立幂等、独立成功或失败，最多 10 份、并发 2。
- 上传成功后服务端立即自动调用现有 P3 处理器；不自动 release。请求中断后材料仍留在隔离区，可用现有 process/retry 恢复，不宣称无人值守队列。
- 当前只用已锁定的 pypdf 规则生成法规字段和报告标题/日期/摘要候选；扫描型 PDF 只标 `OCR_REQUIRED`，低置信字段留空。
- confidence 是未校准的机器线索，不是准确率；每个非空候选至少有一个页码证据。
- pdf-inspector 只提供默认关闭的供应商中立 shadow 合同；不安装 0.2.6，不进入 API 进程，不影响 P3 主链。
- 只有文档已 `ready + released + clean + preview ready`，且服务端给出 `confirm_policy_draft`，才能人工确认；source + version + analysis + audit 单事务提交。

## 文件地界

允许新增 `f1_0011_material_intake.py`、`features/material_intake/**`、P3/P5 材料录入前后端组件、本任务三份文档和窄合成主链验证器 `infra/f1/local_material_verify.py`；允许修改 `models.py`、P3/P5 router/service/contracts、P3/P5 types/API、P3文档库/详情、App 路由，以及仅用于挂载该验证器、把现役 head/34表目录和旧31表备份恢复兼容到 `f1_0011` 的本地运维脚本、Compose 与既有合同断言。旧 migration、requirements/lock、F1.1.1 证据只读。

不新增 OCR、LLM、联网法规抓取、RAG/索引、自动 release、自动审核/发布；不把正文、文件名、对象 key、凭据写入日志或 audit。

## 数据与 API

- `material_analysis`：绑定企业、document_version、source SHA 和分析版本；保存 profile、状态、确认结果与幂等摘要。
- `material_page_classification`：逐页 text/scanned/mixed、OCR required、table/two-column flags、字符数和未校准置信度。
- `material_field_candidate`：法规字段与报告标题/日期/摘要候选、值、页码、短证据、confidence ppm、producer；append-only。
- `GET /v1/ingestion/versions/{id}/material-intake`：供应商中立分析快照与 allowed_actions。
- `POST /v1/policy-workflow/material-analyses/{id}/confirm`：人工最终 source/version 字段；单事务返回 source + draft version。

## 完成条件

1. PDF 上传无需再点“开始安全处理”；最多 10 份批量队列可逐项显示结果，分析详情能展示页级提示与候选，确认后只生成 draft、不自动审核或发布。
2. 唯一 Alembic head 为 `f1_0011`；新表 FORCE RLS，跨租户不可见；pdf-inspector 运行时仍 OFF，未新增依赖。

实现阶段只运行一个直接相关的最小检查；用户随后授权“正常验证”，因此另执行专属实库迁移和一份无客户数据的合成文本 PDF 垂直主链。仍未覆盖真实 OCR、真实客户材料、准确率评估、E2E 或发布验收。
