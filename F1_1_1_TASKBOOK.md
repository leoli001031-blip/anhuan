# F1.1.1 任务书：封住租户越权并重建可信验收

你是执行者，本书是唯一任务来源；没人可问，拿不准的写`BLOCKED.md`，跳过继续别项，随交付提交。
断线先读`PROGRESS.md`最后一节；每完成一项立即追加回执。
目标：把“能跑但假绿”的F1.1修成不可越权、可恢复、可干净重建且证据可信的登记Fixture闭环。
冲突时：原件/租户/正文安全 > 验收真实性 > 数据一致 > 可恢复 > 覆盖率 > 速度。
“只允许/不许”违反即失败；“建议”可换，但须在`PROGRESS.md`记原因。

## 我替领导拍的板

- 猜的：只做F1.1.1纠偏，不进F2、不加功能/换供应商；猜错只延后一轮，不碰真实客户/Gold/生产。
- 猜的：Applied的0001/0002不改，新增`f1_0003`修权限、约束和恢复；禁止stamp/手改版本表。
- 猜的：旧v0.2保留并加`revocation.json`；v0.3是唯一新结论，不抹历史。
- 本轮新授权`.dockerignore`、`.gitignore`与`requirements/requirements-f1.lock`的必要修正；不追认上一轮白名单违规。
- 仍只接26份登记Fixture；任意新资料、OCR、恶意文件扫描、准确率和专业结论继续关闭。

## 界限

只允许改/建：`.dockerignore`、`.gitignore`、`infra/f1/**`、`src/platform_foundation/f1/**`、`src/web/**`、`requirements/requirements-f1.lock`、`tests/{f11_reverse_verify.py,test_f1_*.py,test_f11_*.py,test_f111_*.py,f111_*.py}`、`artifacts/f1-platform-shell/v0.2/revocation.json`、`artifacts/f1-platform-shell/v0.3/**`、本书，并追加`PROGRESS.md/BLOCKED.md`；其余只读。
冻结：F0-A～J1、26原件/F0-I库/key、F1 migration 0001/0002和v0.2三旧文件。只清本轮`anhuan-f111-*`；共享库/卷不删。`infra/f1/ragflow/logs/**`先记SHA/大小/泄漏数，再只清该F1日志并迁至0700非仓库目录；不碰F0-J1日志。
禁止提交凭据、DSN、正文、问答、文件名、企业名、邮箱、电话、向量或对象URL。

## 现状与任务0

2026-08-08实测：`main@54de318`、clean；root/F1 head=`f0d_0006/f1_0002`；15服务healthy；定向49/OK、静态813；旧reverse真实exit2/`orphan_objects=1`。v0.2十五项为常量0；RLS接受非成员自设tenant；DEFINER默认PUBLIC且bridge收任意F0-I tenant；Compose无`build:`/digest并依赖latest/dist；RAGFlow日志约9.5MB、0644且含QA明文。
先复跑`git status --short`、两套heads、Compose ps、49项suite和旧reverse；初始差异只许本书。不得先清孤儿再测。数字不符则脱敏原始输出置`BLOCKED.md`顶部，停止真实Fixture，只做scratch；通过后写≤10行开工回执。

## 任务1：数据库和身份边界先封死

新增0003：所有DEFINER固定安全`search_path`、先REVOKE PUBLIC、只grant最小角色；bridge不收调用者tenant/sub，必须由OIDC sub→membership及当前F1企业派生F0-I tenant。RLS核真实membership，非成员自设tenant也读写0；Worker仅由原子claim的task派生tenant。API不用worker/migration DSN或secret；未知role拒绝；运行时不解析migration DSN。
邀请consume只用OIDC身份，忽略客户端sub；claims逐字段等于ledger，JTI+profile+membership+audit单事务；禁越级/覆盖角色。Audit仅auditor/admin可读；全部API写逐项对账。真实PG/HTTP反测membership、PUBLIC函数、任意F0-I tenant、邀请伪造/半提交，均受控拒绝。

## 任务2：真正恢复、幂等和HTTP E2E

上传先校验/算SHA再查幂等；同企业同SHA重传返回同一document/task，零新增。新请求单事务预留document/task/outbox，再写opaque object/etag；失败只补偿本run同etag对象。实现outbox dispatcher；enqueue失败留待发，Worker用lease token+CAS，双Worker不重复索引，重启续跑。`request_id`绑定企业+question SHA，换问题409；LLM引用不属于PG复核集合即拒答。
重写reverse：随机run_id，快照DB/MinIO/RQ/RAGFlow/audit；业务只走Keycloak+HTTP，禁直调内部函数/插业务行。停Worker→multipart上传→确认各1 object/document/task/outbox/job→启Worker到done→QA/citation/audit；再测Redis断开、Worker SIGKILL、RAGFlow失败、重复上传、A/B互查404。finally按run_id自清并与前快照相等；检查异常/依赖不可达一律非0。

## 任务3：干净重建、日志与不可伪造产物

第三方镜像/Dockerfile base全用`@sha256`；API/Worker/Web均有`build:`，Web多阶段`npm ci`，不挂dist/用latest/nightly；Python锁含版本+hash。`git clone --no-local`到随机scratch，以随机project/端口、fresh F1 DB/RAGFlow卷执行no-cache build→E2E→销卷→重建再跑，禁借预构建image/dist/旧F1数据。
RAGFlow日志不bind仓库；日志/trace仅ID/SHA/长度/计数/reason。用正文/问题/PII/凭据/DSN/向量canary扫docker logs、挂载、trace、artifact，命中0且0700/0600。v0.2标rejected；v0.3生成器亲自执行验收并绑定stdout SHA，缺项/非0拒绝READY，禁常量/default。SBOM为有效CycloneDX，覆盖镜像、API/Web、Python/npm锁。

新增≥36项F1.1.1独立测试，静态总数≥849；旧813个测试名不减，旧断言只许恢复/加强。定向skipped0；全仓实际≥815、skipped≤3且只许旧探针类。依次运行Compose clean build、`test_f111_*.py`、`tests/f111_reverse_verify.py`、npm ci/lint/build、全仓、v0.3双跑。
reverse严格打印：`valid_http_e2e=0 membership_spoof=0 public_definer_exec=0 arbitrary_f0i_tenant=0 invite_spoof=0 role_escalations=0 enqueue_recovery=0 worker_restart=0 duplicate_effects=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 runtime_plaintext_leaks=0 clean_rebuild=0 upstream_mutations=0 scratch_residuals=0`。

## 规矩

禁止skip/todo、删测试、放宽断言、mock核心组件、阈值式“无重复”、按库名绕过RLS/migration、吞异常、`|| true`假绿、硬编码结果、先清理后取证。测试只增；同一验收连败3次换项；变差即回滚本轮改动并记录；最多8轮。

## 完成条件

1. 非成员/PUBLIC/API/Worker均无法跨F1或F0-I租户；真实HTTP上传在断Redis/杀Worker/RAGFlow失败/重复请求后可恢复且零重复、孤儿、错引、审计缺口、新运行时明文。
2. tracked-only clean checkout可二次从零重建；≥849测试、v0.3真实绑定且双跑一致，F0-A～J1/26原件/F0-I零漂移，旧v0.2明确撤销且未升级为生产/准确率。
每项贴实际命令输出，须含红→绿；只说完成不算。`BLOCKED.md`空也写“无”；8轮满即停并如实交付卡点。
