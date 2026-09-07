# EXECUTION_CLOSEOUT_PROGRESS.md

## 开工回执（T0，2026-09-07）

1. HEAD=`fef4a51`，分支=`codex/material-report-aeco-polish`，工作树干净；origin/main=`150b71c`（PR #15 合并），HEAD 已包含于 main。
2. 迁移单一 head=`f1_0024`；默认工程锁 `f1_0014`，material-RAG 锁 `f1_0016`。
3. 基线 12 组测试 = `Ran 120 / OK`（与规格第一节核实一致）。
4. 阶段顺序：T0→T1→T2→T3→T4→T5→T6→T7；T5 恢复验收及 T4 受影响场景在 T6 完成后的最终 HEAD 上重跑。
5. 修改范围：`src/web/src/`（客户服务/整改/报告/材料/任务状态）、`src/platform_foundation/f1/`（API/业务/worker/存储/状态/权限/配置）、`infra/f1/`（候选专属配置/线性迁移/验证工具）、`tests/`、`.github/workflows/`、交付模板与状态文档。
6. 关键未知项：服务器部署指纹是否等于 PR #15（未核对，不声称）；真实域名/凭据/付费预算无新授权（只做预检与回滚包）；OCR/报告质量需专业人员评审（只做离线评测器与待评包）。

---

## T0：当前事实与可重复验收入口

**状态：已完成**

### 已完成
- [x] 核对 HEAD=`fef4a51`/分支/工作树干净/main=`150b71c`（HEAD 已包含于 main）
- [x] 跑基线 12 组 = `Ran 120 / OK`
- [x] 建统一验收入口 `scripts/acceptance_gate.py`（offline/integration/browser/restore/quality 五模式）
  - offline 已验证：backend 120 OK + frontend lint/build/verify OK = PASSED
  - integration/browser/restore/quality 待对应阶段实现后接线
- [x] 证据文件输出到 `out/acceptance/acceptance_offline_*.json`（含 SHA、套件、计数、stdout_tail）

---

## T1：业务闭环（创建服务→录入→整改→复核→关闭）

**状态：前端实现完成 + 五轮复核缺陷全部关闭（PR #19）；浏览器 E2E 待执行**

### 已完成
- [x] `ClientFindingDetailPage.tsx`：客户绑定的问题详情页，支持 编辑/开始整改/提交整改/复核通过/退回/关闭 全流程
- [x] `ClientFindingCreatePage.tsx`：客户绑定的问题录入页（due_at 必填 DatePicker、描述必填——第三轮缺陷修复）
- [x] `ClientRectificationPage.tsx`：加入「录入问题」按钮 + 行级链接跳转详情
- [x] App.tsx 路由：`/console/clients/:clientId/rectification/new` + `/:findingId`
- [x] 迟到响应保护：refreshEpoch 递增 + 全路径校验（第四轮缺陷修复）
- [x] 提交副作用保护：onSuccess 后置 + actionSeq 槽位 + 上下文切换显式重置（第五轮缺陷修复，探针 8/8）

### 待完成
- [ ] 浏览器操作链验证（组件级探针已绿，非浏览器 E2E）

---

## T2：任务状态可理解（投递/重试/故障/恢复）

**状态：部分完成（delivery 状态列 + 退避封顶 + fail-closed 预算）；运维状态投影接口与前端展示待实现**

---

## T3：报告软归档与恢复

**状态：完成（PR #19 关闭第五轮全部 4 项缺陷：恢复回归/审核冻结/生成双路径门/列表过滤 + 前端筛选恢复入口）；实库重放 10/10 PASS**

### 待完成
- [ ] 归档/恢复并发竞态注入验证
- [ ] 归档/恢复入口浏览器 E2E

---

## T4：OCR/报告质量验收样本与评测器

**状态：部分完成（离线评测器框架 + 合成样本合同）；完整独立预期清单与报告评价维度待建立**

---

## T5：部署/HTTPS/恢复配置预检

**状态：部分完成（预检/回滚模板就绪）；版本指纹自动化、HTTPS 预检脚本化、隔离 Linux 恢复链待执行**

---

## T6：文件对账+最小权限+OCR复用

**状态：待开始**

---

## T7：CI+事实矩阵+最终交接

**状态：部分完成（统一验收入口已可用且离线模式全绿 177 项）；`.github/workflows/` 未创建；集成套件与现役 delivery 契约失配待同步**
