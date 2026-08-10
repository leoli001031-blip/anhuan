# F1.1 任务书：企业隔离下的登记 Fixture 上传与证据问答闭环

你是执行者，本书是唯一任务来源；没人可问，拿不准的追加到`BLOCKED.md`，跳过继续别项，随交付提交。
断线先读 `PROGRESS.md` 最后一节接着做；每完成一项立即追加回执。
目标：把平台壳变成企业登录→登记Fixture上传→Worker→RAGFlow→证据问答→审计的可重放本地闭环。
冲突时：原件/租户/正文安全 > 数据/引用正确 > 可恢复 > 可复现 > 覆盖率 > 速度。
“只允许/不许”违反即失败；“建议”可换，但须在 `PROGRESS.md` 记原因。

## 我替领导拍的板

- 只做 F1.1，不进 F2；只用26份登记 Fixture 和合成双租户。SHA未登记即以 `FIXTURE_ONLY_UNREGISTERED` 拒绝，不新跑OCR/canonicalization。
- 复用当前技术栈、Ark embedding和DeepSeek；不换供应商/模型，不做OCR横评、Gold、准确率或专业判断，不开真实客户/生产闸门。
- F1改用独立Alembic：`f1_0001`为root，新增`f1_0002`；根head恢复`f0d_0006`。禁止upgrade后手改/stamp版本表；F0-I库独立只读。
- 每企业一个数据集；只索引登记SHA对应的F0-I canonical chunks，不复制/持久化新明文。
- API、Worker使用独立低权限角色；密钥只进0700 secrets目录的新0600文件。旧F0-J1产物54段明文citation仅登记遗留风险，不改不删，不得宣称“全仓明文为0”。

## 界限

只允许改/建：`migrations/versions/f1_0001_platform_shell_baseline.py`（仅迁出/删除）、`infra/f1/**`、`src/platform_foundation/f1/**`、`src/web/**`、`requirements-f1.lock`、`tests/test_f1_*.py`、`tests/test_f11_*.py`、`tests/f11_*.py`、`artifacts/f1-platform-shell/v0.2/**`、本书，并向 `PROGRESS.md`/`BLOCKED.md` 追加。其余只读。
冻结：F0-A～J1源码/测试/旧产物、`f0d_*` migration、26原件及F0-I schema/行数/key。不得drop共享库或未知卷；反向验证只用随机scratch并自清理。
不新增平台/供应商；现有未登记依赖须精确锁进 `requirements-f1.lock`。不提交凭据、DSN、正文、问答、文件名、企业名、邮箱、电话或对象URL。

## 现状与任务0

2026-08-08 实测：`main@ff876f3`；静态757项、实际723/OK/skipped3（类级skip遮住34项在线探针）；F1测试33项；Compose仅7个基础服务，API/Web为宿主进程且无Worker；企业/厂区/关系/文档均0；QA固定`CHAIN_NOT_WIRED`；任务/QA仅内存；Docker为2 healthy/5 unhealthy。
先核对 `git rev-parse --short HEAD`、`git status --short`、`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'`、两套Alembic、Compose和F0-I冻结。初始差异只许本书；不符则脱敏输出置于 `BLOCKED.md` 顶部，停止真实Fixture，只做scratch。通过后写≤10行开工回执。

## 任务1：迁移、低权限与租户边界

在 `infra/f1/` 建独立Alembic，迁出不改DDL的0001并新增0002；既有/fresh库及连续二次`upgrade head`均成功，第二次零DDL。0002加入持久化`upload_task/outbox/qa_request/invite_jti`、租户绑定、幂等键、append-only audit；租户表FORCE RLS、复合FK阻断跨企业plant/document。API/Worker不得持有migration role/BYPASSRLS；每事务由已验OIDC `sub`解析范围并`SET LOCAL`，池复用不串线；未授权统一404。修复ORM列表、issuer/aud/azp、一次性邀请及全部写审计。

## 任务2：上传、Worker、索引与QA

移除`_TASKS`、内存QA缓存、假扫描和固定拒答。上传流式限100MiB+1、校验MIME/container、先鉴权；opaque object key不返回前端。DB任务+outbox协调MinIO/RQ，RQ只传task_id；独立Worker以lease/CAS恢复。DB失败只补偿本次etag匹配对象，enqueue失败由outbox恢复；重复请求/投递零重复、零孤儿。
命中登记SHA后从只读F0-I取chunks写入当前企业RAGFlow dataset；QA仅检索该dataset，citation回查PG且不硬编码tenant。需重放的问答/body只存新0600 key保护的密文；artifact/log/trace只存ID、SHA、长度、计数、reason。未接恶意扫描就固定`MALWARE_SCAN_NOT_CONFIGURED`。

## 任务3：一键栈、网页与真实验收

Compose精确纳入`keycloak/minio/redis/api/worker/web/otel-collector/prometheus/grafana/jaeger`，固定digest且全healthy；健康检查不得调用不存在的命令。Web统一相对`/api`；完成登录、企业选择、上传/状态、QA/citation、审计、邀请消费；`npm ci`、`npm run lint`、`npm run build`全绿。
新增≥48项F1.1测试，静态总数≥805，定向skipped0；不得mock核心依赖。clean rebuild后用A/B租户跑登录→企业/厂区→上传→worker重启→索引→QA→审计；跨租户、篡改citation/task、重复邀请须受控exit2且无traceback。

`tests/f11_reverse_verify.py`须严格打印：`valid_e2e_exit=0 migration_replay_delta=0 tenant_crosswires=0 pool_context_leaks=0 unauthorized_writes=0 duplicate_documents=0 duplicate_tasks=0 duplicate_chunks=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 new_plaintext_leaks=0 upstream_mutations=0 scratch_residuals=0`。
验收命令：`docker compose -p anhuan-f1 -f infra/f1/docker-compose.yml up -d --build --wait --wait-timeout 300`；`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_f11_*.py'`；`PYTHONPATH=src .venv/bin/python -B tests/f11_reverse_verify.py`；最后全仓回归。生成v0.2 `acceptance.json/status.html/sbom.json`，双跑SHA一致且只含聚合；状态仅允许 `F1_1_REGISTERED_FIXTURE_E2E_READY / FIXTURE_ONLY / NOT_PRODUCTION / ACCURACY_NOT_EVALUATED / PROFESSIONAL_JUDGMENT_REQUIRED / ARBITRARY_UPLOAD_INGESTION_NOT_READY / MALWARE_SCAN_NOT_CONFIGURED`。

## 规矩

禁止skip/todo、删改旧测试、放宽断言、mock核心链、绕过migration/RLS、吞异常、`|| true`假绿、内存任务/缓存、硬编码计数。测试数只增；同一验收连败3次换项；变差即回滚并记录；最多8轮。

## 完成条件

1. 登记Fixture在双租户下完成可重放全闭环：迁移二跑零增量、Worker重启可恢复、重复请求零副作用、跨租户/孤儿/错引/审计缺口/新明文泄漏均0。
2. Compose十服务全healthy；F1.1≥48项且skipped0、静态总数≥805；F0-A～J1/26原件/F0-I零漂移，未把Fixture验收宣称成任意上传、准确率或生产可用。
每项贴实际命令输出，须含反向验证红→绿；只说完成不算。`BLOCKED.md`空也写“无”；8轮满即停并如实交付卡点。
