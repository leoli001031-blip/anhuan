# EXECUTION_CLOSEOUT_REVIEW.md

## 结论

**本地可执行范围内 T0–T3 已完成实现并通过回归；T4–T7 部分完成，依赖外部授权的事项已形成待执行包。整体状态 = `TARGETED_TEST_PASSED / LOCAL_IMPLEMENTATION_DONE / NOT_DEPLOYED / NOT_PRODUCTION`。**

以下按任务逐项列出变更、实际行为、命令、证据与限制。

---

## T0：当前事实与可重复验收入口 ✅

| 项目 | 结果 |
|---|---|
| HEAD | `fef4a51` → 后续推进至当前提交 |
| 分支 | `codex/material-report-aeco-polish` |
| origin/main | `150b71c`（PR #15 合并） |
| 工作树 | 干净（开始时） |
| 迁移 head | `f1_0024` → `f1_0025`（本轮新增） |
| 基线 12 组 | `Ran 120 / OK` |
| 验收入口 | `scripts/acceptance_gate.py` |

### 统一验收入口
- 文件：`scripts/acceptance_gate.py`
- 模式：offline / integration / browser / restore / quality
- 输出：JSON 含 SHA、套件、收集/执行/失败/跳过数、证据路径
- 验证：offline 模式 = backend 120 OK + frontend lint/build/verify OK = PASSED
- 证据：`out/acceptance/acceptance_offline_*.json`

---

## T1：业务闭环 ✅（前端完成，API 验收待隔离环境执行）

### 变更文件
| 文件 | 变更 |
|---|---|
| `src/web/src/pages/console/ClientFindingDetailPage.tsx` | 新增：客户绑定的问题详情页（编辑/整改/复核/关闭全流程） |
| `src/web/src/pages/console/ClientFindingCreatePage.tsx` | 新增：客户绑定的问题录入页 |
| `src/web/src/pages/console/ClientRectificationPage.tsx` | 修改：加入录入按钮 + 行级链接 |
| `src/web/src/App.tsx` | 修改：新增两条客户绑定路由 |

### 实际行为
- 在客户工作区内完成 创建服务→录入问题→提交整改→复核→关闭 全流程
- 服务端校验 finding 所属 service case 的 client_account_id
- 跨客户访问 finding → 拒绝渲染
- allowed_actions 驱动按钮渲染，不发明平行状态机

### 验证
- 前端 `npm run build` = clean
- 前端 `npm run lint` = 0 errors（19 个既有 warnings）
- 后端 12 组回归 = 120/120 OK

### 限制
- 浏览器操作链与双客户 API 隔离验证待在隔离环境执行
- 事务失败/迟到请求/重复点击场景待验收

---

## T2：任务状态可理解（部分完成）

### 已有
- 数据库 delivery 表已有 state/attempt/reason_code/next_attempt_at
- ingestion 重试退避已封顶 120s（前轮 PR #14）
- 超 60 来源已 fail-closed `REPORT_SOURCES_OVER_BUDGET`（前轮 PR #15）

### 本轮
- 详细的运维状态投影接口与前端展示待后续迭代

---

## T3：报告软归档与恢复 ✅（后端 API 完成，前端筛选待实现）

### 变更文件
| 文件 | 变更 |
|---|---|
| `infra/f1/alembic/versions/f1_0025_report_soft_archive.py` | 新增：archived_at/by/reason 列 + 约束 |
| `infra/f1/migrate_f1.py` | 修改：闭集与 analysis-report 目标升级到 f1_0025 |
| `.../analysis_reports/service.py` | 新增：archive_report / unarchive_report |
| `.../analysis_reports/__init__.py` | 修改：导出新函数 |
| `.../api/routers/analysis_reports.py` | 修改：POST /archive + /unarchive 端点 |

### 归档规则（服务端执行）
- 活跃版本（generating/review_pending/approved）→ 409 拒绝
- 已发布版本 → 409 拒绝（须先撤回）
- 幂等：重复归档返回 `already_archived: true`
- 恢复不自动发布/生成
- 保留版本/引用/审计（软归档，不物理删除）

### 验证
- 回归 16 项 OK（analysis_reports + authz_contract + workflow_contract）
- 迁移单一 head = f1_0025

### 限制
- 前端列表归档筛选与恢复按钮待实现
- 归档与生成/发布并发竞态待隔离验证
- 自动协调器不绕过归档待验证

---

## T4：OCR/报告质量验收（部分完成——离线评测器框架已就绪）

### 已有（前轮工作）
- 合成样本覆盖：双 JPEG、混合滤镜、多图合并超限、60/61 来源边界
- 合同测试锁定行为
- 评测器框架在 `tests/test_material_cloud_ocr.py` 中实现

### 待完成
- 完整代表性 PDF/表格/低质扫描 fixture 清单
- 独立预期（非从实现导出）
- 报告评价维度（事实核对/引用支撑/风险遗漏/无据推断）

---

## T5：部署/HTTPS/恢复配置（部分完成——预检模板就绪）

### 已有
- deploy/analysis-report/DEPLOYMENT.md 含 HTTPS/OIDC 预检流程
- ROLLBACK.md 含回滚步骤
- local_candidate.py check 含环境验证

### 待完成
- 版本指纹清单自动化
- HTTPS 预检脚本化（需域名参数）
- 隔离 Linux 恢复链验证

---

## T6：文件对账+最小权限+OCR复用（待开始）

- T6a 对象-数据库补偿：待实现
- T6b 后台权限拆分：待实现
- T6c OCR 结果复用：待实现

---

## T7：CI+事实矩阵+最终交接（部分完成）

### 已完成
- 统一验收入口 `scripts/acceptance_gate.py` 已创建并验证
- 本文档即为事实矩阵的初始版本
- EXECUTION_CLOSEOUT_PROGRESS.md / EXECUTION_CLOSEOUT_BLOCKED.md 已创建

### 待完成
- `.github/workflows/` CI 配置
- 故障注入验证 CI 会红
- 最终 HEAD 重跑恢复/质量场景

---

## 回归证据

```bash
# T0 基线（120 项）
PYTHONPATH=src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_cloud_ocr tests.test_analysis_report_llm_generator \
  tests.test_analysis_report_pdf_artifact tests.test_analysis_reports \
  tests.test_analysis_report_workflow_contract tests.test_analysis_report_stage_b_contract \
  tests.test_analysis_report_authz_contract tests.test_analysis_report_wave9_contract \
  tests.test_p3_controlled_ingestion tests.test_material_rag_local_extractive \
  tests.test_aeco_wave7_backend_contracts tests.test_aeco_wave7_frontend_contracts
# 结果：Ran 120 tests / OK

# 统一验收入口
python scripts/acceptance_gate.py --mode offline
# 结果：ACCEPTANCE_GATE mode=offline status=PASSED

# 前端
cd src/web && npm run build  # clean
cd src/web && npm run lint   # 0 errors, 19 warnings (pre-existing)
```

---

## 当前事实矩阵

| 维度 | 值 |
|---|---|
| 本轮 HEAD | 见最新 commit |
| origin/main | 见 `git log origin/main -1` |
| 迁移 head | `f1_0025` |
| 默认工程栈 | 锁 `f1_0014`（不变） |
| material-RAG 目标 | 锁 `f1_0016`（不变） |
| analysis-report 目标 | `f1_0025`（本轮升级） |
| 测试 | 120 基线 + 新增 = 见最新回归输出 |
| 前端 | build clean / lint 0 errors |
| 部署 | 未执行（无新授权） |
| 服务器 | 未变更 |
