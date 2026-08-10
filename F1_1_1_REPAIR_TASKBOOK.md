# F1.1.1 修复轮：封住边界并重建可信验收

> **PAUSED / HISTORICAL CONTRACT。** 当前权威见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)：F1.1.1 为 `F1_1_1_PAUSED_NOT_ACCEPTED`，不得自动恢复 formal/reverse/SBOM/clean/M4。下文原合同保留；其中“不commit”已被用户后来对三个本地 checkpoint 的明确授权覆盖，但仍无 push、部署或验收通过。

你是执行者，本书是唯一任务来源；中途没人可问，拿不准的写`F1_1_1_REPAIR_BLOCKED.md`，跳过继续别项，最后随交付提交。
断线先读`F1_1_1_REPAIR_PROGRESS.md`接着做；每完成一项立即更新。
目标：把已被独立审计否决的F1.1.1修成租户不可越权、上传/索引/QA可恢复、clean checkout可重建且证据不可假绿的登记Fixture闭环。
冲突时：租户/正文/冻结证据安全 > 验收真实性 > 数据一致 > 可恢复 > 覆盖率 > 速度。
“只允许/不许”违反即失败；“建议”可替换，但须在进度里记原因。

## 我替领导拍的板

- 只修F1.1.1，不进F2；真实客户、Gold、准确率、专业结论、任意上传与生产继续关闭。
- Claude已退出；本轮只由Codex在独立worktree执行，不回送Claude，不commit/push。
- 已应用风险：`f1_0003`只读，新建线性`f1_0004`；历史中的开发期敏感材料不改史、不复述，只登记聚合并保证新内容为0。
- 旧v0.3结论作废；修复后也只能签`F1_1_1_ACCEPTED_FIXTURE_ONLY`。
- 现存共享对象/库/卷只做前快照；不得先清。故障与清理只针对随机`anhuan-f111-repair-*`资源。

## 界限

只允许改/建：`F1_1_1_REPAIR_{TASKBOOK,PROGRESS,BLOCKED}.md`、`.dockerignore`、`.gitignore`、`infra/f1/**`、`src/platform_foundation/f1/**`、`src/web/**`、`requirements/requirements-f1.lock`、`tests/{f11_support.py,f11_reverse_verify.py,test_f1_*.py,test_f11_*.py,test_f111_*.py,f111_*.py}`、`artifacts/f1-platform-shell/v0.2/revocation.json`、`artifacts/f1-platform-shell/v0.3/**`；其余只读。
冻结F0-A～J1、26原件、F0-I、F1 migration 0001～0003、旧v0.2三文件、主工作树与PDF Probe worktree。不得读`.env*`或输出secret/DSN/JWT/正文/文件名/路径/PII/向量/object URL。
不得删除共享数据、改历史、stamp版本表、安装未锁依赖；必要项写BLOCKED后继续。

2026-08-09用户选择方案1后的最小例外：仅为formal随机隔离，可修改下列参数边界及其攻击测试；缺少`F111_F0_ISOLATION_CONFIG`时旧默认行为、测试数量与skip语义必须完全不变，且不得改26原件、旧migration、历史库或共享Docker/RAGFlow/provider：`src/platform_foundation/f0_isolation.py`、`src/fixture_router/router.py`、`src/platform_foundation/f0i/{config.py,bootstrap.py,keyfile.py,locking.py}`、`src/platform_foundation/f0e/{acceptance.py,runtime_config.py,supervisor.py}`、`src/platform_foundation/f0f/{acceptance.py,runtime_config.py,supervisor.py,keyfile.py}`、`src/platform_foundation/f0g/{config.py,tokens.py}`、`src/platform_foundation/f0h/{runtime_config.py,supervisor.py}`、`tests/test_platform_foundation.py`、`tests/test_f0e_local_ocr.py`、`tests/test_f0f_controlled_body_gold.py`、`tests/test_f0g_fixture_annotation.py`、`tests/test_f0h_ppocrv6_runtime.py`、`tests/test_f0i_canonical_chunks.py`、`tests/test_f0j0_ragflow_probe.py`、`tests/test_f0j0_retrieval_probe.py`、`tests/test_f0j1_retrieval_qa.py`、`tests/test_f111_f0_isolation.py`。该例外只允许显式配置私有0700根、0600凭据、随机loopback PostgreSQL、私有语料/密钥和唯一UUID Docker项目；禁止过滤full discover、monkeypatch、新增skip或事后清共享资源假绿。full discover必须精确保留HEAD原有3个class-level skip（两组J0与一组J1，因本UUID项目不存在），不得启动J栈或连接真实provider；formal targeted仍要求skipped=0。

## 现状与任务0

2026-08-09实测：worktree=`codex/f1-1-1-repair@262bf9f`且clean；root/F1 head=`f0d_0006/f1_0003`；静态测试名850、F1.1.1测试37；Codex预审=7类P0+6类P1；旧reverse会在快照前删共享业务行，禁止执行；B线PDF Probe已验收且不集成。
先核`git status --short`、两套`alembic heads`、850个测试名、38个既有变更与白名单；只读快照DB/MinIO/RQ/RAGFlow/audit/容器/卷/冻结SHA，记录legacy数量但不清。任一不符，脱敏原始输出置BLOCKED顶部，真实Fixture停止，只做scratch。通过后在进度写≤10行开工回执。

## 任务1：身份与数据库边界

新增`f1_0004`：撤销API直接写membership，只允许受控函数按真实OIDC sub+既有管理员membership授权；`enterprise_user`也FORCE RLS。邀请用行锁或单条条件UPDATE保证并发仅一赢家，JTI+profile+membership+audit同事务，禁越级/覆盖角色。全部DEFINER重核PUBLIC/角色/search_path；API/Worker不得持有对方或migration材料。
红→绿覆盖：非成员自设tenant后自铸会员、角色提权、两连接并发消费、PUBLIC/跨F0-I tenant、连接池复用；未授权统一404/0行。

## 任务2：恢复、幂等与QA原子性

为task加不可复用`lease_token`；claim/index/finish均token-CAS，stale worker零副作用。Outbox稳定RQ job_id并原子dispatch；同企业同SHA并发/重放只得同一document/task/object/index，写对象失败可补传，补偿仅删本run且etag匹配对象。
QA先原子reserve `(tenant,request_id,question_sha)`和owner token；异问题409，同问题重放，终态owner-CAS。回答必须实际引用，citation=LLM引用∩PG复核集合；业务写和audit同事务。
真实scratch反测Redis断开、双Worker跨lease、SIGKILL恢复、RAGFlow失败、重复dispatch/上传、同/异问题并发、空/伪造/跨租户citation。

## 任务3：证据、复现与产物

重写reverse：取证前`DELETE/TRUNCATE/reset`计数必须0；业务只走OIDC+HTTP，前后精确比较DB/MinIO/RQ/RAGFlow/audit，legacy单列且本轮delta=0，finally只清run_id资源。严格打印20项：`valid_http_e2e membership_mint invite_double_consume stale_lease_commit duplicate_dispatch upload_replay_effects enqueue_recovery worker_restart ragflow_recovery qa_request_races citation_crosswires tenant_crosswires audit_gaps object_orphans_delta rq_orphans_delta index_duplicates preclean_mutations new_plaintext_leaks upstream_mutations scratch_residuals`，全部`=0`。
tracked-only随机clone两轮执行no-cache build→迁移重放→HTTP E2E→down -v→零残留；不得借已有image/dist/DB。日志/trace/卷/artifact canary命中0。v0.3生成器亲跑迁移、定向、全仓、reverse、npm ci/lint/build、两轮rebuild及SBOM对账；任一失败不产READY，三产物原子写、0600、双跑SHA一致。CycloneDX须与实际镜像、Python/npm锁逐项一致。

新增≥25项独立测试，静态总数≥875；旧850个测试名不减，定向skipped0，全仓不得新增skip。先跑攻击定向，再reverse、npm、全仓、artifact失败门与成功双跑。

## 规矩

禁止skip/todo、删测试、放宽断言/阈值、mock核心链、硬编码结果、吞异常、`|| true`、按库名绕过、先清理后取证。测试只增；同一验收连败3次换项；变差就回滚本轮文件并记录；最多8轮。

## 完成条件

1. 真实PG/HTTP/MinIO/Redis/RQ/RAGFlow下，租户/邀请/lease/上传/QA攻击全受控拒绝或恢复，20项reverse全0，本轮重复/孤儿/错引/audit缺口/新明文均0。
2. 两次clean checkout可从零重建；≥875测试、v0.3真实双签一致，冻结与PDF Probe零漂移，状态仅`F1_1_1_ACCEPTED_FIXTURE_ONLY`及非生产边界。
每项贴实际命令输出，含红→绿；只说完成不算。BLOCKED空也写“无”；或8轮满即停，如实交付卡点。
