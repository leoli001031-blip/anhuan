# EXECUTION_CLOSEOUT_REVIEW.md

## 结论

**截至 2026-09-08 第七轮独立复核后的返工（PR #23，合并提交 `81b9243`）：报告页串客户已确认关闭；归档审计以事务身份（xmin）绑定重写同事务证明，跨事务伪造与同事务重复消费均被数据库拒绝。T2/T4/T5/T6/T7 的范围性工作仍未完成。整体状态 = `WORK_IN_PROGRESS / NOT_DEPLOYED / NOT_PRODUCTION`。**

七轮独立复核与对应修复：

| 轮次 | 发现 | 修复 PR |
|---|---|---|
| 第一轮（5 项） | OCR 页覆盖/证据截断/批量取消/重试/HTTPS | #14、#15 |
| 第二轮（3 项） | 混合过滤 fail-closed/61 源预算/合并文本上限 | #15 |
| 第三轮（5 项） | 创建空日期/迟到响应串客户/归档三层阻断/迁移目标失配/验收入口退出码 | #17 |
| 第四轮（2 项） | refreshEpoch 从未递增/归档后仍可生成发布 | #18 |
| 第五轮（4 项） | 恢复归档回归/归档草稿仍可提交审批/旧提交清空新客户表单/列表未过滤 | #19 |
| 第六轮（2 项） | 报告页保留旧客户归档目标可跨客户提交/归档审计未实现且恢复抹掉历史 | #21 |
| 第七轮（1 项） | 时间戳先后不构成同事务证明，跨事务可伪造审计事件 | #23 |

以下按任务逐项列出变更、实际行为、命令、证据与限制。

---

## T0：当前事实与可重复验收入口 ✅

| 项目 | 结果 |
|---|---|
| HEAD | `fef4a51` → 后续推进至当前提交 |
| 分支 | `codex/material-report-aeco-polish` |
| origin/main | `81b9243`（PR #23 已合并） |
| 工作树 | 干净（开始时） |
| 迁移 head | `f1_0025` → `f1_0026`（第六轮新增审计表） |
| 基线 12 组 | `Ran 120 / OK` |
| 验收入口 | `scripts/acceptance_gate.py` |

### 统一验收入口
- 文件：`scripts/acceptance_gate.py`
- 模式：offline / integration / browser / restore / quality
- 输出：JSON 含 SHA、套件、收集/执行/失败/跳过数、证据路径
- 验证：offline 模式 = backend 120 OK + frontend lint/build/verify OK = PASSED
- 证据：`out/acceptance/acceptance_offline_*.json`

---

## T1：业务闭环 ✅（前端完成；详情页生命周期已通过五轮复核修复并有探针证据）

### 第三~五轮复核修复（PR #17/#18/#19）
- 创建页：due_at 改为必填 DatePicker、描述必填（契约验证通过）
- 详情页迟到响应：`refreshEpoch` 在挂载/切换/卸载/refresh 时递增，读与动作全路径校验
- 详情页提交副作用（PR #19）：清表单/关弹窗移到通过 epoch 校验后的 `onSuccess`；`actionSeq` 序号槽位使旧动作 finally 不得清新动作 loading；切换上下文显式重置表单
- 证据：离线 TSX 生命周期探针 8/8 通过（迟到读/迟到错误/迟到动作/卸载/旧整改提交/旧复核提交/旧 finally/正常路径），见 `out/soft_archive_fix_verify_2026-09-08/probe_frontend_fix.json`

### 限制
- 浏览器 E2E 全链未执行（探针为组件逻辑级验证）

### 实际行为
- 在客户工作区内完成 创建服务→录入问题→提交整改→复核→关闭 全流程
- 服务端校验 finding 所属 service case 的 client_account_id
- 跨客户访问 finding → 拒绝渲染
- allowed_actions 驱动按钮渲染，不发明平行状态机

### 变更文件
| 文件 | 变更 |
|---|---|
| `src/web/src/pages/console/ClientFindingDetailPage.tsx` | 新增：客户绑定的问题详情页（编辑/整改/复核/关闭全流程） |
| `src/web/src/pages/console/ClientFindingCreatePage.tsx` | 新增：客户绑定的问题录入页 |
| `src/web/src/pages/console/ClientRectificationPage.tsx` | 修改：加入录入按钮 + 行级链接 |
| `src/web/src/App.tsx` | 修改：新增两条客户绑定路由 |

---

## T2：任务状态可理解（部分完成）

### 已有
- 数据库 delivery 表已有 state/attempt/reason_code/next_attempt_at
- ingestion 重试退避已封顶 120s（前轮 PR #14）
- 超 60 来源已 fail-closed `REPORT_SOURCES_OVER_BUDGET`（前轮 PR #15）

### 本轮
- 详细的运维状态投影接口与前端展示待后续迭代

---

## T3：报告软归档与恢复 ✅（PR #21 后完整：guard 三分支 + 审核/生成全冻结 + 列表过滤 + 前端筛选恢复入口 + 上下文绑定 + 只插审计）

### 第六轮复核修复（PR #21）
- **报告页上下文绑定**：列表数据、待归档目标与动作绑定客户上下文代次（contextEpoch）；切客户立即清空旧列表/弹窗/目标/request ID（旧行不可点）；`submitArchive` 提交前校验目标绑定代次，弹窗残留直接丢弃不发起写请求；完成回调按代次 + 动作序号双重校验。探针 6/6：A 弹窗→切 B→确认零 API 调用、B 加载期间旧行消失、迟到列表抑制、正常流程/卸载行为保持。
- **归档审计（f1_0026）**：新表 `f1.analysis_report_management_event`（FORCE RLS、provider-admin 策略、对 f1_api 只授 SELECT + 列级 INSERT，无 UPDATE/DELETE）。插入 guard 重验 actor、绑定会话企业，并以**事务身份**证明与报告行更新同事务：报告行 `xmin` 必须等于 `pg_current_xact_id()`（掩码 32 位）；时间戳先后不构成证明（第七轮教训，已整体移除该比较）。同一事务对同一报告/动作的重复插入被 `REPORT_MANAGEMENT_EVENT_DUPLICATE` 拒绝并整体回滚。`report_archived` 的 reason 必须与报告行一致。archive/unarchive 在 UPDATE 与 commit 之间写事件；幂等早退不重复记录。实库 19/19：归档恰好 1 条带 reason/actor 事件、重复归档/重复恢复零新增、空报告两事件齐全、f1_api 篡改被拒、脱离事务伪造被拒、**T1 先行→T2 归档→T1 伪造被拒**、同事务双插被拒并原子回滚。

### 变更文件（累计）
| 文件 | 变更 |
|---|---|
| `infra/f1/alembic/versions/f1_0025_report_soft_archive.py` | archived_at/by/reason 列 + 约束 + 列级授权 + guard 三分支（归档/恢复/业务）；downgrade 恢复 f1_0023 原 guard 并回收授权 |
| `infra/f1/migrate_f1.py` | 闭集与 analysis-report 目标升级到 f1_0025 |
| `.../analysis_reports/service.py` | archive_report / unarchive_report；apply_transition 行锁下拒绝已归档 submit/return/approve/publish；generate 两条派发路径（新请求 + 精确请求恢复/重投）均行锁校验归档 |
| `.../analysis_reports/repository.py` | list_provider_reports 默认 `archived_at IS NULL`，显式 include_archived；客户端侧 published 列表/详情排除归档（纵深防御） |
| `.../api/routers/analysis_reports.py` | POST /archive + /unarchive 端点；GET 列表 include_archived 查询参数 |
| `src/web/src/pages/console/ClientReportsPage.tsx` | “显示已归档”筛选 + 归档（原因 ≤500 入审计）+ 恢复入口；归档行不再链接工作台 |
| `src/web/.../adapters/*` | 契约：摘要携带 archived_at；archive/unarchive 适配（HTTP + Mock） |

### 归档规则（服务端 + 数据库双层执行）
- guard 三分支：已归档 → 仅归档列可变（版本指针/可见性冻结，混入业务字段即拒）；恢复 → 仅归档列可变（混入业务更新 `ANALYSIS_REPORT_UNARCHIVE_MIXED`）；未归档 → 原 f1_0023 业务规则
- 活跃版本（queued/generating/review_pending/approved/published）→ 409 拒绝归档
- 已归档报告：submit/return/approve/publish 全部 409（行锁下，withdraw 按独立契约保留）；generate 新请求与精确请求恢复/重投均 409
- 客户侧 published 列表/详情/健康分不含归档报告
- 幂等：重复归档 `already_archived: true`；恢复不自动发布/生成；恢复后可继续合法流程

### 验证（PR #19 HEAD `501151b`；PR #21 `3caec47` 扩展至 17/17；PR #23 `2d45971` 扩展至 19/19）
- 实库重放（完整迁移 f1_0025、真实 service/RLS/delivery/worker、确定性生成器）10/10 PASS：空报告归档→恢复 ✓；默认列表隐藏/显式含归档可见且带时间戳 ✓；归档后 submit/approve/publish 全拒且状态零漂移 ✓；新请求 generate 拒 ✓；同请求 resume 拒 ✓；queued 阻止归档 ✓；恢复后 submit→approve→publish 成功且客户可见 ✓；容器/卷/网络残留 0
- 新增 20 项离线合同测试（`tests.test_analysis_report_soft_archive`，已注册进验收入口）
- 离线门 184/184 OK + 前端 lint/build/verify OK（PR #21）

### 限制
- 归档并发竞态（两管理员同时归档/恢复）未做实库竞态注入
- 浏览器 E2E 未执行（归档/恢复入口为静态合同 + 适配层验证）

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
# 统一验收入口（offline 模式现含 15 组）
python scripts/acceptance_gate.py --mode offline
# 结果（PR #19 HEAD 501151b）：Ran 177 tests / OK；frontend lint/build/verify OK；status=PASSED

# 实库归档生命周期重放（第五轮复核场景 1:1 复现）
python out/soft_archive_fix_verify_2026-09-08/replay_probe.py
# 结果（PR #23 后）：19/19 PASS（含跨事务伪造/同事务重复消费拒绝），迁移 head=f1_0026，容器/卷/网络残留 0

# 详情页生命周期探针（实际 TSX + hooked React）
node out/soft_archive_fix_verify_2026-09-08/probe_frontend_fix.cjs
# 结果：8/8 PASS

# 前端
cd src/web && npm run build  # clean
cd src/web && npm run lint   # 0 errors, 22 warnings (pre-existing)
```

---

## 当前事实矩阵

| 维度 | 值 |
|---|---|
| 本轮 HEAD | `2d45971`（PR #23，合并提交 `81b9243`） |
| origin/main | `81b9243`（PR #23 已合并） |
| 迁移 head | `f1_0026`（单一 head） |
| 默认工程栈 | 锁 `f1_0014`（不变） |
| material-RAG 目标 | 锁 `f1_0016`（不变） |
| analysis-report 目标 | `f1_0026` |
| 离线测试 | 184（15 组：原 12 组 120 + closeout 迁移 + p2_wave1 + soft_archive 27） |
| 实库重放 | 第五~七轮缺陷场景 19/19 PASS（含审计生命周期/幂等/不可变/事务身份绑定） |
| 前端 | build clean / lint 0 errors / tsc clean |
| 集成套件 | `test_analysis_report_postgres_integration` 仍与现役 delivery 契约失配（20 项过期断言），待同步 |
| 部署 | 未执行（无新授权）；服务器 demo 栈未变更 |
