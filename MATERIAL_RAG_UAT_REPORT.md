# MATERIAL RAG UAT Report

日期：2026-08-18 23:22
起点：checkpoint `a72fdb186de2ab53f6c8d72983f1b24fc99dac1e`，branch=`codex/material-rag-scanner-protocol`
现役状态：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_NOT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_DEFERRED / NOT_PRODUCTION`
禁止标签：未写 `HUMAN_UAT_READY` / `UAT_PASSED` / `RELEASE_VERIFIED` / production。2026-08-18 机器门运行当时未 commit / push / 部署；不代替领导签字；未跑 `material-rag-uat-open`。

2026-08-19 口径更新：Ark key 轮换不再阻塞其他开发；真实 live retrieval 仍未测试，保持延后，不改变本报告 2026-08-18 的历史运行证据。

## 2026-08-18 23:22 收口（机器门通过；本窗口）

目标检查：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红：`Ran 63`，`FAILED (failures=6)`。`tenantDisplayValue` 合同缺失；`SWITCH_FN_MISSING`×5（delayed-portal / leftover-qa / refuse-bad-options / commit-failed / steps-evidence）。
- 绿：同一命令 exit 0，`Ran 63 tests in 5.314s` / `OK`，failures/errors/skipped=0。一跳后再绿 `Ran 63 tests in 5.329s` / `OK`。`git diff --check=0`。

本窗口 Live 周期1：

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
```

- start 2026-08-18T23:14:05+0800 → 23:14:40，wall=34510ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:62243/qa`；`resource_identity_verified=1`；`shared_identity_unchanged=1`；`human_uat_url_ready=1`；`LOCAL_MATERIAL_RAG_UAT_STARTED`
- check 23:14:40 → 23:15:41，wall=61523ms，exit 2：反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_SWITCH_FAILED`
- check `finally` 已 down。未补 stop（栈已空）。

本窗口 Live 周期2（一跳后复验）：

- start 23:20:48 → 23:21:22，wall=34217ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:63153/qa`。
- check 23:21:22 → 23:21:31，wall=8668ms，exit 0：反向门同上。
- 摘要：`journeys_passed=6`；J6 五字段全 1；`valid_tenant_count=2`；`cross_tenant_state_isolated=1`；`cross_tenant_citation_denied=2`；`cross_tenant_delete_isolated=1`；唯一 `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`
- stop 23:21:31 → 23:22:05，wall=33477ms，exit 0：`C=0 V=0 N=0`；`LOCAL_MATERIAL_RAG_UAT_STOPPED`。未跑 `material-rag-uat-open`。

保全：`/private/tmp/anhuan-material-rag-tenant-switch-20260818`（0700/0600，shared-before 纳入根锚）。detached_root=`e9125ab16ef596c3bb019d287a69398f69b2350a96c139d5a938f04363770b64`。旧 `j6-clear-20260818` / `j6-select-20260818` / `journey-gate-20260818` 只读未覆盖。

## 2026-08-18 22:45 收口（未通过；历史，已被 23:22 覆盖）

目标检查：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红：`Ran 58`，`FAILED (failures=6)`。`FRESH_EMPTY_FALSE_GREEN` / `ID_MISMATCH_FALSE_GREEN` / `J6_CLEAR_FN_MISSING` / `J6_SUMMARY_FN_MISSING`；localctl 尚无 J6 摘要校验。
- 绿：同一命令 exit 0，`Ran 58 tests in 3.034s` / `OK`，failures/errors/skipped=0。`git diff --check=0`。

本窗口 Live 周期1：

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
```

- start 2026-08-18T22:38:02+0800 → 22:38:37，wall=34658ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:57636/qa`；`resource_identity_verified=1`；`shared_identity_unchanged=1`；`human_uat_url_ready=1`；`LOCAL_MATERIAL_RAG_UAT_STARTED`
- check 22:38:48 → 22:39:49，wall=60926ms，exit 2：反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_SWITCH_FAILED`
- check `finally` 已 down。

本窗口 Live 周期2（一跳后复验）：

- start 22:43:06 → 22:43:41，wall=34828ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:58198/qa`。
- check 22:44:00 → 22:44:41，wall=41111ms，exit 2：反向门同上。
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_TENANT_OPTION_MISSING`
- check `finally` 已 down。专属 C/V/N=0。控制目录已删除。未再跑 stop（栈已空）。未跑 `material-rag-uat-open`。未跑第三次 live。

未出现要求的 live 成功摘要：五个 J6 字段全 1 / `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。

保全：`/private/tmp/anhuan-material-rag-j6-clear-20260818`（0700/0600，shared-before 纳入根锚）。detached_root=`47e968d32defc0e53c418a9ad137764b9fb178418eba6a6c5099a73247b20f12`。旧 `j6-select-20260818` 与 `journey-gate-20260818` 只读未覆盖。

## 2026-08-18 20:24 收口（通过；历史，J6 清空假绿，已被 22:45 覆盖）

目标检查：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红：`Ran 53`，`FAILED (failures=12, errors=1)`。FakePage 禁止 `clickElementWithText`；缺三阶段码与六键；antd6 fake 必须点可见唯一 enabled exact wrapper 才提交。
- 绿：同一命令 exit 0，`Ran 53 tests in 2.822s` / `OK`，failures/errors/skipped=0。

本窗口唯一 fresh live：

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
./scripts/localctl material-rag-uat-stop
```

- start 2026-08-18T20:21:09+0800 → 20:21:45，wall=36060ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:61181/qa`；`resource_identity_verified=1`；`shared_identity_unchanged=1`；`human_uat_url_ready=1`；`LOCAL_MATERIAL_RAG_UAT_STARTED`。重叠二次 start 被锁拒绝，不计入新周期。
- check 20:23:41 → 20:23:50，wall=9123ms，exit 0。
- 反向四门：`LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 六旅程摘要：`{"authorization_header_present":1,"cleared_on_failure":true,"conflict_409":1,"cross_tenant_citation_denied":2,"cross_tenant_delete_isolated":1,"cross_tenant_state_isolated":1,"denied_404":1,"enterprise_header_present":1,"human_uat_url_ready":1,"journeys_passed":6,"residual_count":0,"resource_identity_verified":1,"shared_identity_unchanged":1,"stage":"material-rag-uat","uat_actor_header_present":0,"unavailable_503":1,"valid_tenant_count":2}`
- J6：`journeys_passed=6` 含 `J6_FAIL_CLEAR`；`unavailable_503=1` 对应 HTTP 503；runner 在观察到 UI POST 后置 `request_seen=1` 再校验 phase `unavailable`。成功路径不打印六键 JSON。
- 唯一尾码：`LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`
- stop 20:24:10 → 20:24:43，wall=33644ms，exit 0：`C=0 V=0 N=0`；`shared_identity_unchanged=1`；`LOCAL_MATERIAL_RAG_UAT_STOPPED`
- 专属 C/V/N=0。控制目录已删除。共享 `anhuan-f1` C=15/V=9/N=1 全部 exited。未跑 open。未跑第二次 live。未跑 161 / `material-rag-verify`。外发=0。

保全：`/private/tmp/anhuan-material-rag-j6-select-20260818/cycle1`（0700/0600）。`DETACHED_ROOT.txt` detached_root=`2df34a471482e8c2a1803feb8594202f76cb5ed9a9f256947435c3f4454e05a5`。旧 `journey-gate-20260818` 只读未覆盖。

## 2026-08-18 19:43 收口（未通过；历史，旧窗口 live 2/2 已封存）

目标检查：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红（一跳前）：`Ran 48`，`FAILED (failures=3)`。`antd6-content` → `REQUEST_NOT_SENT`；`query-uncommitted` / `ask-disabled` → `EVIDENCE_INVALID`。
- 绿：同一命令 exit 0，`Ran 48 tests in 2.662s` / `OK`，failures/errors/skipped=0。

本窗口 Live 周期1：

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
```

- start 2026-08-18T19:21:15+0800 → 19:21:50，wall=35038ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:54291/qa`；`resource_identity_verified=1`；`shared_identity_unchanged=1`；`human_uat_url_ready=1`；`LOCAL_MATERIAL_RAG_UAT_STARTED`
- check 19:22:04 → 19:23:03，wall=58392ms，exit 2：反向门 `LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED REQUEST_NOT_SENT`（无旅程 JSON）
- check `finally` 已 down。

本窗口 Live 周期2（一跳后复验）：

- start 19:41:20 → 19:41:55，wall=35071ms，exit 0：`HUMAN_UAT_URL http://127.0.0.1:56113/qa`。
- check 19:42:08 → 19:43:10，wall=61236ms，exit 2：反向门同上。
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED REQUEST_NOT_SENT {"actual_phase":null,"expected_phase":"unavailable","http_status":null,"journey":"J6_FAIL_CLEAR","request_seen":0}`
- check `finally` 已 down。专属 C/V/N=0。控制目录已删除。未再跑 stop（栈已空）。未跑 `material-rag-uat-open`。未跑第三次 live。

未出现要求的 live 成功摘要：`valid_tenant_count=2` / 三项 cross-tenant / `LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`。

保全：`/private/tmp/anhuan-material-rag-journey-gate-20260818/cycle1` 与 `cycle2`（0700/0600，无正文无凭证）。

## 2026-08-18 18:53 收口（未通过；旧窗口）

目标检查：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  /Users/lichenhao/Desktop/安环项目/.venv/bin/python -B -m unittest \
  tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红：`Ran 41`，`FAILED (failures=8)`（隔离映射、CRM 排序、overlay labels、资源门、浏览器 seed/human stage）。
- 绿：同一命令 exit 0，`Ran 41 tests in 0.885s` / `OK`，failures/errors/skipped=0。

Live 周期2：

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
```

- start exit 0：`HUMAN_UAT_URL http://127.0.0.1:64405/qa`；`resource_identity_verified=1`；`shared_identity_unchanged=1`；`human_uat_url_ready=1`；`LOCAL_MATERIAL_RAG_UAT_STARTED`
- check exit 2：`LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 浏览器：`LOCAL_MATERIAL_RAG_UAT_BROWSER_FAILED LOCAL_BROWSER_VERIFY_FAILED UAT_PHASE_MISSING`
- check `finally` 已 down。专属 C/V/N=0。控制目录已删除。未再跑 stop（栈已空）。未跑 `material-rag-uat-open`。

未出现要求的 live 摘要键：`valid_tenant_count=2` / `cross_tenant_state_isolated=1` / `cross_tenant_citation_denied=2` / `cross_tenant_delete_isolated=1`。

## 2026-08-18 17:34（历史，三个 UAT 通过标签已撤销）

日期：2026-08-18 17:34
当时现役（已撤销）：`UAT_MACHINE_GATE_PASSED / LOCAL_SYNTHETIC_BROWSER_UAT_PASSED / HUMAN_UAT_READY / HUMAN_UAT_SIGNOFF_PENDING / LIVE_RETRIEVAL_UAT_BLOCKED_BY_KEY_ROTATION / NOT_PRODUCTION`

## 红 → 绿（鉴权接线，先红后修）

命令：

```
PYTHONPATH=$PWD/src F1_KEYCLOAK_ISSUER_URL=http://material-rag.invalid/realms/anhuan \
  python -B -m unittest tests.test_material_rag_uat tests.test_engineering_closeout_browser_runner
```

- 红：`Ran 36`，`FAILED (failures=4, errors=7)`。失败签名：`mount_if_enabled(app)` 缺失；单 UAT flag 仍 mount；`set_test_client_binder` 缺失；browser stage `material-rag-uat` 缺失。
- 绿：同一命令 exit 0，`Ran 36 tests in 0.571s` / `OK`，failures/errors/skipped=0。不低于修改前 31 项。

## 前端默认关闭

- `src/web` 下 `npm run lint`：exit 0（沿用 sibling `node_modules` 符号链接，本仓未 `npm install`，跑完已删除链接）。
- `src/web` 下 `npm run build`（无 `VITE_MATERIAL_RAG_UAT_LOCAL=1`）：exit 0。产物中无 UAT Vite=1。CRM「在该客户域检索」仅 UAT build 显示，文案含「本地合成」。
- `git diff --check`：exit 0。

## Live gate 1/2（随后立即 stop）

```
./scripts/localctl material-rag-uat-check
./scripts/localctl material-rag-uat-stop
```

- check exit 0。stop exit 0。
- 反向门聚合：`LOCAL_MATERIAL_RAG_UAT_REVERSE {"default_404":1,"foreign_404":1,"role_403":1,"unauth_401":1}`
- 浏览器六旅程：`{"stage":"material-rag-uat","journeys_passed":6,"cleared_on_failure":true,"residual_count":0,"authorization_header_present":1,"enterprise_header_present":1,"uat_actor_header_present":0,"denied_404":1,"conflict_409":1,"unavailable_503":1}`
- 唯一尾码：`LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK`
- 专属 C/V/N=0。控制目录已删除。共享 `anhuan-f1` 指纹前后相同（15 exited / V=9 / N=1）。

未跑第二次 live gate。未跑 161 项，未跑 `material-rag-verify`。未触碰共享栈。未读/用旧 embedding key。外发=0。

## 接线合同

- UAT router 同时要求 `F1_MATERIAL_RAG_UAT_LOCAL=1` 与 `F1_LOCAL_ENGINEERING=1`。`main.py` 只调用一次 `material_qa_uat.mount_if_enabled(app)`。
- 复用正式 `tenant_from_header` + `require_role("super_admin","enterprise_admin","plant_admin")`。删除 `X-Uat-Actor`。企业只来自已认证 tenant。
- 真实 CRM client 先 `get_account`（租户 RLS 只读）再映射合成槽位；未知/他租户统一 404 `MATERIAL_CONTEXT_NOT_FOUND`。
- 默认 compose/build 仍关闭。公共 `POST /api/v1/material-qa` 自由提问仍在 DB/网络前拒绝。

## 复现命令

```
./scripts/localctl material-rag-uat-start
./scripts/localctl material-rag-uat-check
./scripts/localctl material-rag-uat-stop
```

专属 project/image/ports，overlay 只打开两端 UAT flag、`F1_LOCAL_ENGINEERING=1`、`F1_EXTERNAL_PIPELINES_ENABLED=false`。不挂载 embedding key。跑完即清理。
