# A_ECO_VISUAL_PROGRESS

## 健康度视觉落地与真机复审（2026-08-23）

- [x] 参照方向 1 的客户首页、健康度详情桌面版和 390px 窄屏版，落地客户只读的安环管理健康度入口与详情页。
- [x] 本地显式 mock 展示固定 `60/100`、六项维度、改善优先级与评分依据；真实 HTTP 模式不推导分数，显示 `暂不评分`。
- [x] 真机复审发现并修正：390px 顶栏换行阻断、桌面内容过密、mock 导航误显问答、Progress 旧属性警告。
- [x] 390px 使用品牌＋功能菜单＋已验证企业两层结构；菜单只含首页、分析报告和退出登录；页面无横向溢出。
- [x] 参考图与实现图已在同一画布对照；全页、窄屏首屏与评分条局部证据见 `.audit/compare-health-*-final-v2.png` 和 `.audit/compare-health-summary-focused-final.png`。
- [x] `design-qa.md` 已记录修复、允许偏差和证据，`final result: passed`。
- [x] 目标 TypeScript 检查 exit=0；`git diff --check` exit=0；临时 Vite 配置、依赖软链接、浏览器页签和专属 demo 均已收口。

现役状态：
`A_ECO_HEALTH_VISUAL_IMPLEMENTED / HTTP_SCORE_FAIL_CLOSED / BROWSER_VISUAL_QA_PASSED / SCORING_BACKEND_CONTRACT_PENDING / NOT_COMMITTED / NOT_PRODUCTION`

## 开工回执（任务 0，2026-08-23）

- 目标：把 A-Eco 从“桌面结构已落地”推进到“甲方演示前可复审”——封 3 个 P1（窄屏阻断、无效问答入口、客户可见测试文本），再桌面收口。
- 顺序：任务 0 隔离基线 → 任务 1 P1×5（顶栏/侧栏与表格/目录与审核栏/问答入口/测试文本）→ 任务 2 桌面收口 → 任务 3 最小验证（lock SHA → build → diff --check → rg 清零 → 白名单核对）。
- 最大风险：本条线索的前端与此前 worktree 版本不同（导航更少、QA 已有禁用态、有引用抽屉），改动前必须先读实码，避免按旧记忆误改。
- 基线核对：源 HEAD=6cdbba3 ✓；源 dirty=3M+7??（部署包与状态文档/测试），src/** 无 dirty ✓；branch 与目标目录原先不存在 ✓。

## 进度

- [x] 任务 0 隔离基线（worktree: material-rag-analysis-report-visual-polish-20260823/source/repo，branch: codex/material-report-aeco-polish）
- [x] 任务 1.1 Portal 顶栏 390px：flex-wrap 双行结构，品牌 nowrap，邮箱 <768 隐藏，导航整行等分 tab（PortalLayout + index.css .portal-*）
- [x] 任务 1.2 Console：<1280 隐藏固定侧栏改「菜单」抽屉；客户表 <768 改列表形态（ClientsPage/useNarrow）；客户报告表同处理（ClientReportsPage）
- [x] 任务 1.3 报告目录 <768 顶部横向滚动条（.report-nav-col/.section-nav media）；审核台 Row/Col→.workbench-grid，<1280 正文在上审核区在下；UAT data-* 属性原样保留
- [x] 任务 1.4 HTTP 隐藏问答导航（isMockData 条件渲染），/portal 默认进报告；直达 QA 给说明+「查看分析报告」CTA（QaPage HTTP 分支重写）
- [x] 任务 1.5 客户可见测试文本清零：generator.py 去掉 status_summary 的指纹片段、改写 citations/usage_boundary 文案（顺带把 digest 变量移除以满足 rg 清零）；fixture 材料展示名 arfix-*-current → 「服务商共享制度汇编/企业安环基础制度」（内部 label/object_key/sha 不变）；CRM 展示名不改——src/web/scripts/engineering-browser-verify.mjs:128 断言原名，不在白名单
- [x] 任务 2 桌面收口：报告阅读宽度/章节层级保持；客户列表加摘要行（共 N 家 · 服务中 M 家）；章节导航滚动定位当前态（IntersectionObserver + aria-current）；全站 :focus-visible 焦点样式；审核台 ≥1280 保持左右结构
- [x] 任务 3 最小验证
  - `shasum -a 256 src/web/package-lock.json` = `8f8f9288…c9d4b` ✓ 与任务书一致
  - symlink 复用 `/Users/lichenhao/Desktop/安环项目/src/web/node_modules`（未 install）
  - `npm --prefix src/web run build` → **exit=0**（tsc -b + vite build ✓，3223 modules；1.58MB chunk 警告为既有性能债）
  - 已删除本任务创建的 symlink 与 `src/web/dist`
  - `git diff --check` → 无输出 ✓
  - `rg '本报告由本地确定性夹具生成|citations 列表|fingerprint_sha256\[:12\]' generator.py` → 无命中（exit 1）✓
  - 改动全部在白名单内，staged=0 ✓；源工作树 dirty 保持 3M+7?? 未变 ✓
  - 未 commit / 未 push

## 收尾

- A_ECO_VISUAL_BLOCKED.md：无。
- 检查一次通过，未达 3 次失败线；无需回滚。
- 真实双身份截图复验留待独立审核（本任务不声明 VISUAL_ACCEPTANCE）。

最终状态：
`A_ECO_VISUAL_FIX_IMPLEMENTED / FRONTEND_BUILD_PASSED / BROWSER_VISUAL_REVIEW_PENDING / NOT_COMMITTED / NOT_PRODUCTION`
