# P5 POLICY WORKFLOW 任务书

> **已完成的历史执行合同。** P5 当前为 `P5_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文保留启动基线。

## 状态与边界

- P4已收口为`P4_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`；P5在同一未提交工作树继续，不回退P2-P4改动。
- 唯一线性迁移为`f1_0008_policy_workflow`，`down_revision=f1_0007`；`f1_0001`至`f1_0007`只读，禁止第二Alembic head。
- P5只做法规/政策来源登记、版本候选、内部审核发布状态、影响候选任务和分域检索；不联网抓取、不做法规适用结论、不替代专业判断、不外部发布或通知。
- 只允许内部合成/fixture来源和P3已`ready + released + clean + preview ready`的受控文档引用；不复制正文、文件名、object key或外部token。
- 固定边界：`CANDIDATE_ONLY / INTERNAL_REVIEW_ONLY / NOT_LEGAL_ADVICE / PROFESSIONAL_JUDGMENT_REQUIRED / NOT_PRODUCTION`。
- 不恢复F1.1.1 formal/reverse/SBOM/clean/M4；不commit、不push、不部署、不删除共享数据。

## 产品闭环

登记法规来源 → 建立版本候选 → 关联已受控文档证据 → 提交审核 → 审核通过或退回 → 内部发布版本状态 → 建立企业影响候选 → 分配/推进影响任务 → 按领域、状态与效力候选检索。

## Task 0：合同与并行地界

- 主agent单写任务文档、`f1_0008`、`models.py`、API main、App/Layout和既有共享文件。
- Backend子任务只新增`src/platform_foundation/f1/features/p5/**`与`src/platform_foundation/f1/api/routers/p5_policy_workflow.py`。
- Frontend子任务只新增`src/web/src/features/p5/**`；主agent最后一次性接App/Layout。
- 所有新表`ENABLE + FORCE RLS`，PUBLIC/worker无表权，企业复合外键，跨租户详情404/集合零行；不新增SECURITY DEFINER。
- 所有状态变化与audit同事务；审核事件append-only，已发布版本的来源、SHA、日期、领域和摘要不可修改。

## Wave 1：来源与版本候选

### 数据

- `policy_source`：title、publisher、source_type(`law/regulation/standard/guidance/internal`)、jurisdiction、source_reference、status(`active/archived`)、creator和时间。
- `policy_version`：source、version_number、title、domain(`safety/health/environment/fire/chemical/general`)、effect_status(`unknown/not_effective/effective/expired`)、issued_on/effective_from/effective_to、summary、可选P3 document_version_id与其SHA、workflow_status(`draft/in_review/approved/rejected/published/superseded`)、creator和时间。
- 同一source版本号唯一；文档引用必须是同企业、受控进入且已release/clean/preview ready。只保存opaque ID、SHA和结构化元数据，不保存正文/文件名/路径/URL抓取结果。

### API与页面

- `GET/POST /api/v1/policy-workflow/sources`
- `GET/PATCH /api/v1/policy-workflow/sources/{source_id}`
- `POST /api/v1/policy-workflow/sources/{source_id}/versions`
- `GET /api/v1/policy-workflow/versions/{version_id}`
- 页面：`/policies`、`/policies/sources/:sourceId`、`/policies/versions/:versionId`。
- 管理员可建来源/草稿；普通企业成员只读当前企业内已published版本，明确reviewer可见in_review。

## Wave 2：审核与内部发布

- `policy_review_event` append-only：action(`submitted/approved/rejected/published`)、comment、actor、occurred_at；禁止UPDATE/DELETE。
- 状态机：`draft|rejected → in_review → approved → published`；`in_review → rejected`；新published版本会将同source旧published改为superseded。
- 提交者不能审批自己提交的同一轮候选；管理员可submit，reviewer为`super_admin`或本企业`auditor`；只有管理员可执行内部publish。
- API：
  - `POST /versions/{id}/submit`
  - `POST /versions/{id}/approve`
  - `POST /versions/{id}/reject`
  - `POST /versions/{id}/publish`
- 页面按服务端`allowed_actions`展示提交、通过、退回和内部发布；“发布”仅表示本企业内部状态，不对外分发。

## Wave 3：影响候选与待办

### 数据

- `policy_impact_candidate`：version、domain、scope_note、priority(`low/medium/high/critical`)、status(`open/accepted/dismissed`)、creator和时间；仅人工候选，不声明法规适用。
- `policy_impact_task`：impact、title、owner_user_id、due_at、status(`open/in_progress/completed/dismissed`)、creator和时间。
- impact/task写入必须引用已approved/published版本；任务owner必须是同企业成员；completed/dismissed为终态。

### API与页面

- `GET/POST /api/v1/policy-workflow/impacts`
- `GET /api/v1/policy-workflow/impacts/{impact_id}`
- `PATCH /api/v1/policy-workflow/impacts/{impact_id}`
- `POST /api/v1/policy-workflow/impacts/{impact_id}/tasks`
- `PATCH /api/v1/policy-workflow/impact-tasks/{task_id}`
- 页面：`/policy-impact`，展示影响候选、负责人、截止时间和状态；没有短信、邮件、微信或外部工单。

## Wave 4：分域检索与集中接缝

- `GET /api/v1/policy-workflow/search?q=&domain=&effect_status=&workflow_status=`仅检索结构化title/summary/publisher/source_reference，返回当前RLS可见结果；无向量检索、正文索引或外部搜索。
- 页面提供领域、效力候选、工作流状态筛选；明确显示效力为人工候选，不作为法律意见。
- 企业切换取消旧请求并清空数据；Spin/Alert/Empty、桌面表格和窄屏卡片齐备；按钮只看`allowed_actions`。
- 仅修启动/编译、主链阻断、数据损坏、明确跨租户越权、migration head冲突；其他问题登记BLOCKED。

## 验证与状态

- P5最多运行一个预计60秒内的直接相关最小检查；不跑全仓、E2E、coverage、benchmark、生产build或F1.1.1验收。
- 最小检查覆盖`f1_0008 → f1_0007`、状态机、review append-only、published不可变、影响任务终态、关键API/页面合同。
- 未运行标`NOT_TESTED`；通过仅标`TARGETED_TEST_PASSED`。阶段完成使用`P5_COMPLETE_NOT_RELEASE_VERIFIED`，禁止`RELEASE_VERIFIED`。
