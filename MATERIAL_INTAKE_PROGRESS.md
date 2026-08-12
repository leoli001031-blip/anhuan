# MATERIAL INTAKE Progress

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

- `./scripts/localctl migrate` 真实执行 `f1_0010 → f1_0011`，退出码 0，固定结果 `LOCAL_MIGRATE_OK`。
- 新增窄 `material-verifier`，只启动专属 PostgreSQL、MinIO、ClamAV；使用一份无客户数据的合成文本 PDF，通过进程内 ASGI HTTP/API 路由上传与自动处理，依赖服务和数据层均为真实实例。
- 固定结果 `LOCAL_MATERIAL_VERIFY_OK`：扫描 clean=1、预览单元=1、材料分析=1、页=1、未校准候选=4、释放对象=1、政策来源=1、政策 draft=1、自动发布=0。
- 同一确认幂等重放返回相同来源与版本；另一租户 API 404，底层文档、分析、候选、来源与版本 RLS 均为 0 行。
- 随机 scratch 数据库、三个随机 MinIO 桶和对象残留均为 0；现役业务库没有合成材料行。
- 状态提升为 `SMOKE_PASSED / NOT_PRODUCTION`；这不是发布验收或准确率证明。

## 尚未验证

- 未执行真实材料 PDF、批量浏览器流程、OCR、备份恢复实跑或候选准确率评估；仅一份确定性合成文本 PDF 通过。
- 扫描型 PDF 仍只标 `OCR_REQUIRED`，不执行 OCR；confidence 仍是未校准线索。
- 未部署，也未恢复 F1.1.1 发布验收。
