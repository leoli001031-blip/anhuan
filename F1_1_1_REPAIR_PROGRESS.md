# F1.1.1 Repair Progress

> **现役摘要（2026-08-11）：** 本阶段仍为 `F1_1_1_PAUSED_NOT_ACCEPTED`。修复与后续 P2-P8 已由本地 checkpoint `4180709 → 06f0500 → 9d712cd` 保存；未 push、未部署，连续阶段开发已停止。当前总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-09 接管

- Claude 已按用户决定退出；后续仅 Codex 开发与验收。
- 隔离 worktree：`codex/f1-1-1-repair@262bf9f`，主工作树保持只读。
- 当前门禁：`PRE_M4_STATIC_AUDIT_FAILED`，7 类 P0、6 类 P1；旧 v0.3 不可信。
- 已核静态 head：root=`f0d_0006`、F1=`f1_0003`；静态测试名=850，F1.1.1=37。
- 旧 reverse 存在取证前清理，未执行；先完成任务0只读快照与新增红灯测试。
- 顺序：任务0 → f1_0004/身份 → lease/outbox/QA → reverse/rebuild/artifact → M4。
- 最大风险：共享 acceptance 状态与历史敏感材料；全程不先清、不回显。

## 2026-08-09 任务0与0004合同

- 三路只读审计收口：安全8类P0、恢复/QA 9类P0、证据链7类确定性假绿；任务书白名单已补`tests/f11_support.py`。
- 新建线性`f1_0004`，静态唯一head=`f1_0004`；`f1_0003` SHA保持`a8058d…2da9`。
- 0004已声明租户复合FK、enterprise_user FORCE RLS、受控企业/邀请写、无参resolver、Worker lease token、outbox claim、QA owner claim及完整PUBLIC revoke清单。
- models/session context同步；runtime secret目录改显式配置并校验0600，Worker tenant context必须同时携带task+lease token。
- 新增18项migration合同测试。红：`Ran 18 / FAILED(failures=1)`，发现PUBLIC revoke可机检清单缺口；补完整13签名清单后绿：`Ran 18 / OK`。
- 尚未把0004应用到共享DB；下一步只在随机scratch验证迁移与并发攻击。

## 2026-08-09 运行时三线合并与迁移接缝

- 身份安全线新增11项、上传恢复线24项、QA线25项；联同迁移21项共`Ran 81 / OK`，当前静态测试名=931。
- 身份线已改无参membership resolver、OIDC email绑定邀请、一次性邀请锁、同事务audit、0600 key文件与本地角色读闸门。
- 上传线已改reserve→对象回读→finalize、同SHA赢家复用、稳定RQ ID、outbox CAS、lease-token CAS及RAGFlow canonical reconcile；新增周期dispatcher进程与独立健康心跳。
- QA线已改原子reserve四状态、owner CAS、AAD绑定密文、citation正文SHA/页码回查和audit同事务。
- 0004修正legacy outbox按event生成唯一job ID，并把迁移期RLS backfill政策限制为同事务null→非null后立即DROP；过期scanning/indexing可重新派发。
- 真实PG预审发现：FORCE RLS下不能继续让`f0d_migration`作为持久写definer owner。已删除0003两项NULL-context写政策；拟采用NOLOGIN/NOBYPASSRLS专用owner、API/Worker零membership、bootstrap精确ALTER OWNER，待明确授权后落最终最小ACL与攻击测试。
- 未应用共享迁移、未运行旧reverse、未生成新v0.3、未commit/push。

## 2026-08-09 运行时密钥与会话接缝

- 新增统一fail-closed密钥文件边界：F1、provider、F0-I均只接受显式绝对路径或显式目录，regular/owner/nlink/0600/size逐项验证；错误只返回reason code。
- 移除F1运行时代码中的开发机`/private/tmp` MinIO/RAGFlow/Ark/DeepSeek/F0-I路径；Compose改为显式provider目录和F0-I key只读挂载。
- F1 QA不再让冻结F0-J1客户端回退到宿主key路径，改用F1专属DeepSeek客户端与provider secret边界。
- 修复Worker tenant session仅后置SET task/token的接缝：所有带enterprise的Worker会话在创建时同时绑定task_id+lease_token。
- 新增12项secret/session合同测试；migration/security/recovery/QA/secret/artifact合并`Ran 115 / OK`，Compose带显式占位配置`config --quiet` exit=0且无stderr。
- 仍未连接共享DB/服务；专用definer owner与真实scratch攻击矩阵完成前不得应用0004。

## 2026-08-09 reverse 反假绿重写

- 旧reverse未执行；先新增19项纯静态反假绿合同，红=`Ran 19 / failures=17, errors=2`，实证抓到预清、固定配置、共享服务控制、吞清理异常、旧17指标与伪重建。
- 重写为仅随机`anhuan-f111-repair-*` scratch可运行；业务突变只走OIDC+HTTP，依赖缺失/异常一律保持非零并exit=2。
- PG/MinIO/RQ/RAGFlow/audit五平面先快照，legacy orphan/duplicate单列；run registry登记DB ID、object etag、RQ job、remote dataset/document/chunk。
- finally仅按本run精确清理，并要求清理后完整Snapshot与取证前逐字段相等；无宽泛清理、无共享stack控制、无异常正文输出。
- 20项严格顺序已落；`py_compile`绿，静态反假绿=`Ran 19 / OK`；缺scratch配置实跑仅一行20项全非零、exit=2。
- 有效Compose必须叠加tracked repair override并证明所有published port避开共享端口；日志、OTel文件和Jaeger trace均纳入正文canary扫描。
- 尚未跑真实reverse：等待clean scratch提供0600/0700 verifier bundle、4个不同已登记Fixture的opaque manifest及独立host端口；旧v0.3的17指标合同由产物线同步升级。
- 补外置PG误接共享库硬闸：control/worker必须同时连接由随机project派生的`f111_repair_<uuidhex>`数据库，且角色分别为superuser/NOSUPERUSER；该只读scope校验位于首份Snapshot前。
- 反假绿合同扩至22项：`py_compile`、`Ran 22 / OK`、`git diff --check`均绿；空配置运行严格只输出一行20项全`1`并`exit=2`。仍未连接任何服务或运行旧reverse。
- 再封端点接缝：API/Keycloak/MinIO/Redis/RAGFlow/Jaeger host端点必须逐一匹配本随机Compose的published port；API/Worker/dispatcher运行时只能指向同一派生scratch DB及内部scratch服务，真实RAGFlow故障目标修正为Compose服务`ragflow`。
- 泄漏闸新增外部0600 `leak_canaries`清单，连同运行期JWT、Fixture位置/文件名、问题与二进制canary只在内存对日志、trace、QA密文行和允许的本轮输出目录扫描，绝不打印匹配内容。
- 当前静态反假绿=`Ran 26 / OK`，`py_compile`与`git diff --check`仍绿；live 20项未跑，必须由clean scratch提供canary bundle后才可验收。
- cleanup基线身份闸红→绿：首次新增合同`Ran 27 / FAILED(failures=1)`暴露测试未覆盖循环登记；修正后将DB全部可删identity、同次RAGFlow inventory和cleanup授权状态绑定首份Snapshot，任何复用历史ID或未完成基线绑定均禁止清理。
- stale lease不再把通用ACL/SQL异常算成功，只接受renew=false且旧token UPDATE=0；重复dispatch同时要求单outbox、单job、单claim attempt，上传重放要求document/task/outbox/object均精确1。
- Snapshot再纳入Compose服务状态；SIGKILL恢复需`up --wait`后与基线状态一致。RQ inventory包含未登记`rq:job:*`原始hash，避免孤儿藏在queue/registry之外。
- 最终离线合同=`Ran 36 / OK`；`py_compile`、`git diff --check`绿；空环境运行仍严格一行20项全`1`、`exit=2`。未连接PG/HTTP/MinIO/Redis/RQ/RAGFlow，未执行旧reverse。

## 2026-08-09 专用Definer与随机PostgreSQL实测

- 8个NOLOGIN/NOBYPASSRLS/零membership域owner及精确函数owner、ACL、命令级RLS、downgrade owner restore已静态收口；repair合并回归曾达`Ran 131 / OK`。
- 首轮随机空库实测抓到两个真实接缝：Alembic version schema需先由bootstrap以migration owner创建；finalizer的OID owner变更参数需显式text类型。修复后首轮可到`F1_MIGRATE_OK`且重复迁移规范化catalog零漂移。
- 第二个全新随机PostgreSQL scratch从空库重放`f1_0001→f1_0004`成功；第二次迁移exit=0，前后规范化schema-only SHA完全相同。
- 真实f1_api/f1_worker攻击脚本首轮发现`user_profile`未FORCE RLS；新增ENABLE+FORCE并从全新空库重放后，head/schema owner/8角色属性及membership/函数owner及search_path/PUBLIC/RLS/旧migration policy/复合FK/直写/SET ROLE/schema CREATE/非成员可见/迁移写/池GUC共18项全0。
- 当前随机scratch只用于本阶段继续验证，完成后按精确container/volume/directory清理；共享数据库和既有15服务未写入。

## 2026-08-09 运行时凭据最小挂载

- API、Worker、Dispatcher与Keycloak provisioner不再bind整个F1 secret目录；均改为逐文件只读挂载，runtime service看不到bootstrap/migration DSN。
- API仅挂API DB、邀请、QA、MinIO及其所需三项provider；Worker仅挂Worker DB、MinIO、RAGFlow/Ark及F0-I；Dispatcher仅挂Worker DB；Worker不再看到DeepSeek key。
- 新增运行时挂载反向合同；secret/config/keycloak合并`Ran 41 / OK`，全部repair定向`Ran 193 / OK`；随机端口Compose `config --quiet` exit=0且无输出。
- Keycloak JWKS走容器内地址，issuer与浏览器public origin显式由随机host端口注入；tracked realm仍无密码，one-shot provisioner仅见6个合成身份的0600密码文件。

## 2026-08-09 v0.3 authority先行封闭

- 发现public artifact builder可消费伪造全绿JSON晋级后，先把public publish永久降为NONCOMPLETABLE；即使输入全绿证据也只能REJECTED且失败批次无READY字节。
- diagnostic repro runner明确不具备发布authority；当前artifact反假绿`Ran 21 / OK`。
- 正在实现固定formal orchestrator：必须亲自执行迁移、真实PG攻击、定向/全仓/npm、严格20项reverse、两轮clean rebuild、日志canary及SBOM实物对账，任何缺失或红灯均不得晋级。

## 2026-08-09 正式编排与增强PG语义

- 固定formal入口已落：调用者只能给0600数据配置，不能给命令、root、output、evidence或capability；public JSON发布仍永久不可晋级。
- formal新增源码/白名单边界、f1_0001～0003冻结闸、独立0700 HOME/TMPDIR、33项PG指标精确解析、20项reverse、两轮clean结果一致、日志与runtime inventory标记；失败批次不含READY字节。
- PG verifier扩为33项真实语义：受控企业/resolver、邀请越级与双连接唯一消费、upload/outbox token及过期重领、QA同异hash/owner/audit回滚、空库门和finally残留。
- 增强verifier在现有精确随机scratch真实运行exit=0，33项全部`=0`；脚本只输出聚合指标，未触碰共享PG/服务。
- 日志canary离线17项、SBOM reconcile离线10项、formal/artifact/PG接缝联合72项全绿；两者都只接受同一UUIDv4 Compose project并fail closed。
- 首次no-cache api/worker/web构建成功并暴露同tag并发覆盖；repair override已拆分API/Worker/Web三类project唯一tag，后续clean round不得复用首轮镜像。
- 仍未签收：clean rebuild runner、真实全栈20项reverse、全仓/npm、两轮从零重建、v0.3正式双跑与M4暗查未完成。

## 2026-08-09 Clean runner与运行镜像证据整改

- clean runner已固定为两轮各自UUIDv4 project/DB/端口/clean clone/no-cache三镜像；真实迁移重放、33项PG语义、OIDC租户HTTP、日志/trace、down-v与精确残留均fail-closed，离线合同13项绿。
- 首轮真实二次迁移审计暴露`GRANT CONNECT`每次写catalog日志；`migrate_f1.py`现先查`has_database_privilege`，仅缺失时grant，并新增重放幂等合同。
- 独立审计否决了tag-only SBOM：任意同名本地tag可换镜像而旧marker不变。现runtime证据绑定17服务实际container Image ID、3个Dockerfile base实际Image ID、静态locks与固定Docker trust base，并以0600一次性文件交给formal。
- formal只有在marker、runtime evidence、静态inventory三者一致时才可晋级；CycloneDX每个Compose/base组件写实际运行Image ID，保留声明pin为属性。改任一API Image ID会改变正式batch与SBOM SHA。
- 新增完整伪Docker transcript正例及container label/tag/repo digest/base单字段篡改反测；migration/formal/SBOM/clean聚焦`Ran 72 / OK`，py_compile与`git diff --check`绿。
- 已精确删除完成验证的本轮随机PG scratch容器、卷、目录、状态文件及两个旧临时镜像tag；只读复核残留=0，未触碰共享资源。
- 诚实缺口：clean runner目前验证fresh PG、OIDC和租户HTTP，但登记Fixture上传→Worker→索引→QA仍由独立formal reverse承担；需在正式验收前决定并补足clean-round HTTP E2E接缝，当前不得READY。

## 2026-08-09 Codex单线接管与旧测试保全

- 按用户最新决定停止Claude协作，后续由Codex在隔离worktree单线实现、集成和M4验收；主工作树只读且当前无漂移，不commit/push。
- 旧F1/F1.1共89个测试名与数量全部保留；固定端口、共享数据库、源码口令、固定临时路径及冻结F0-I migration DSN已改为仅接受本轮UUIDv4 project、派生scratch DB、随机loopback端口、0700 bundle和0600 secret的fail-closed合同。
- formal子进程固定`PYTHONPATH=src:tests`，解决旧测试顶层helper导入接缝；环境同时绑定formal/reverse同一project、config SHA、随机端口与host侧私有secret副本。
- 新增冻结基线850项逐路径/测试名/重复次数保全闸，禁止用新增静态测试替换或删除旧live回归；当前基线保全实跑通过。
- container向DSN只在0700 runtime home中复制并把bootstrap/migration主机改写为本轮127.0.0.1端口，源bundle不改；文件类型、owner、0600、nlink、总量均逐项验证。
- 当前稳定离线合同：formal+config 37/37、核心repair静态集合233/233、旧隔离config/repro 32/32；`git diff --check`通过。活动中的clean/PG/reverse文件待子任务冻结后再纳入同一全套。
- clean runner现已把严格20项reverse作为每轮登记Fixture HTTP E2E：上传→Worker→索引→QA及故障恢复必须在两个fresh scratch中分别全0；只读F0-I来源复制仍在做源容器身份、前后聚合及双dump不变性收口，未签READY。

## 2026-08-09 Claude会话终止与Formal生命周期闭合

- 按用户明确授权已精确终止`tmux anhuan-f111`；复核为tmux server不存在。未清理Claude工作树、数据库、对象、卷或历史证据，后续只由Codex在本隔离worktree开发与验收。
- formal公开配置已收紧为source-only v2，调用者不能提供project、端口、prepared capability、命令、证据或输出位置。
- `run_formal_acceptance`现内部持有primary生命周期：随机prepare→仅内存评估→源复核与精确cleanup→`assert_closed_clean`→最后唯一publish；prepare失败不产artifact，cleanup失败只能发布REJECTED且无READY字节。
- host verifier bundle固定为本轮UUID项目的0700随机目录、0600文件，并在生命周期结束精确清除；旧prepared-stack authority payload不能重放成公开验收。
- 离线红→绿：生命周期接口接缝初始18 errors；修正后formal 29项+clean API 25项=`Ran 54 / OK`，`py_compile`与`git diff --check`均绿。
- 尚未签收：镜像OCI provenance、结构化clean证据、泄漏正控/候选artifact扫描仍在最后接缝复核；真实primary、两轮clean、全仓/npm、v0.3和M4未运行。

## 2026-08-09 私有输入与冻结F0全仓闸

- 私有输入准备器已把26份登记源对象封装为无路径/文件名的单一受限bundle，并把4份E2E对象复制为opaque私有文件；五份冻结执行证据、F0-I只读scope、provider/F0-I key及问题文件均走0700/0600、原子发布和前后身份校验。
- 输入准备器攻击合同主线复跑`Ran 26 / OK`，`py_compile`与`git diff --check`均绿；尚未运行真实prep或Docker。
- formal命令已改为实际使用prepared checkout内、绑定启动解释器身份的bridge；full delivery摘要、checkout证据、构建输入和后续产物共用同一source authority。
- 独立暗查确认：冻结F0 live测试仍硬绑既有PostgreSQL/Docker/RAGFlow/provider边界，无法仅由formal环境安全重定向。formal现固定`FROZEN_F0_LIVE_ISOLATION_REQUIRED`并fail-closed，不运行共享full discover、不产READY。
- 其余离线接缝继续收口；解除该闸需要用户明确授权修改冻结F0参数边界或提供独立测试命名空间，未授权前不启动真实formal/M4。
- checkout/输入/formal联合105项在两处测试夹具红灯后完成窄修：私有HOME下首次Git工具初始化不再污染source摘要；source restore负向夹具补齐已验证selection前置条件。主线复跑`Ran 105 / OK`。
- 正式编排精确19模块定向门由主线独立复跑`Ran 386 / OK`、skipped=0；全树`git diff --check`为0。当前唯一阻断仍是`FROZEN_F0_LIVE_ISOLATION_REQUIRED`，因此保持NOT_ACCEPTED且未运行真实prep/formal/Docker。

## 2026-08-09 冻结F0隔离入口获授权

- 用户明确选择推荐方案1：允许最小参数化冻结F0 live测试及必要配置，使其只在formal持有的随机UUID PostgreSQL、私有语料/密钥和唯一Docker项目中运行。
- 授权不包含修改26原件、历史数据库、旧migration、共享对象/卷、默认F0行为或完成阈值；三类既有J探针只允许沿用“独立命名栈不存在时class skip”的原语义，不得连接当前共享F1栈或真实provider。
- 实现拆为数据库/语料、Docker/J探针、formal编排三个不重叠战区；隔离合同与全仓回归未转绿前继续`FROZEN_F0_LIVE_ISOLATION_REQUIRED`，不运行真实formal、不产READY。

## 2026-08-09 冻结F0共享隔离合同首绿

- 新增单一`F111_F0_ISOLATION_CONFIG`边界：配置为canonical JSON，凭据仅存4份0600文件；无环境变量时返回legacy分支，有变量但任一身份/权限/schema错误则固定reason fail-closed。
- 资源身份绑定完整RFC4122 UUIDv4、随机loopback PostgreSQL、`/private/tmp/anhuan-f111-repair-f0-*` 0700根、唯一数据库与Docker/J项目；拒绝55432、HOME/repo、短UUID碰撞、跨库/跨角色/外来密码、symlink/hardlink及非canonical配置。
- 配置采用0600 staging、fsync、create-only原子发布；短写/既有目标/残片均有反测。主线独立复跑`tests.test_f111_f0_isolation`=`Ran 17 / OK`。
- 旧F0调用方、私有runtime tree与formal生命周期仍在接线；因此完成闸仍保持`FROZEN_F0_LIVE_ISOLATION_AUTHORIZED_PENDING_IMPLEMENTATION`，未启动Docker/数据库、未产READY。
- formal基线闸抓到5项新隔离测试误入冻结F0文件（599→604）；主线把5项等量迁入新F1.1准备器套件，未删测试。复核旧9文件`test_*`/`SkipTest`签名diff=0、旧方法仍599，新准备器套件=`Ran 37 / OK`。
- F0G/F0I/F0F/F0D调用方增加阶段专属数据库集合，跨阶段目标在connect前拒绝；F0I key/lock与F0G token绑定同完整UUID的私有`tmp_dir`，保留0600/nlink1/固定长度，legacy缺env仍走旧direct-`/private/tmp`分支。
- 主线跨线首跑曾以`Ran 63 / 1 failure`抓到旧能力未接私有根；补齐后独立复跑input+isolation=`Ran 65 / OK`，未使用Docker/数据库。隔离线另跑默认旧F0F/F0G/F0I静态合同89/89绿。

## 2026-08-09 冻结F0隔离离线收口与Docker信任基

- 方案1最小参数化已完成离线终审：旧9个F0文件仍精确599个测试方法、3个原有class skip，默认非live合同89/89；formal隔离定向最初439/439绿。
- 首次真实输入准备诚实失败为`DOCKER_READ_REJECTED`；只读定位到固定launcher是Docker Desktop系统symlink，而旧门在subprocess前把任何symlink拒绝，未触碰容器或共享数据。
- 修复不放宽到PATH/context：只接受固定launcher→固定Docker Desktop二进制、固定SHA-256、不可写的空配置目录和本机`/var/run/docker.sock`；执行前后复核二进制与socket身份，普通读取和dump共用同一门。
- 新增4项攻击测试覆盖合法launcher、hash/socket拒绝、dump同信任门与执行期二进制变更；红灯为4 errors/1后续接口failure，修复后输入准备器48/48绿。
- 全部20模块离线定向重跑=`Ran 443 / OK`、skipped=0；`py_compile`与局部`git diff --check`均绿。下一步仅运行真实私有输入准备，成功前仍不得解除formal阻断或写READY。
- 第二个真实红灯为`F0G_SOURCE_DUMP_REJECTED`：固定migration角色在`FORCE RLS`下被PostgreSQL拒绝完整data-only copy；同参数定向`/dev/null`复现exit=1，未保存正文。
- F0G scope现诚实绑定与F0-I相同的固定本地bootstrap只读角色；聚合在repeatable-read/READ ONLY事务内，dump固定data-only/两schema/无owner/无privilege，均无密码且只走已锁容器。新增角色一致断言后prep48/48、clean46/46绿。
- 正式输入准备实跑成功=`INPUT_PREP_READY`；独立`_verify_bundle`通过，目录4个均0700、文件46个均0600/nlink1/current-owner，source-config摘要=`f11ab663…09defc`。尚未运行formal或发布v0.3。

## 2026-08-09 首轮Formal红灯、Compose发现与来源权威修复

- 首次formal在创建服务前诚实发布不可变REJECTED批次，固定blocker=`COMPOSE_CONFIG_RED`；无READY字节，旧mutable v0.3顶层退役，拒绝批次保留且不删除。
- 根因是formal私有0700 HOME不读取用户Docker配置，而Compose插件只存在于固定Docker Desktop目录；私有HOME现只写canonical `.docker/config.json`（目录0700/文件0600/唯一`cliPluginsExtraDirs`键），插件launcher目标、regular binary、owner/mode及固定SHA在每次Compose调用前后复核。
- 插件合同先红2 errors+1 failure，修复后新增3项攻击合同全绿；调用者`DOCKER_CONFIG`、用户auth/context均不继承，reverse/log/SBOM只通过同一私有HOME发现插件。
- 随后早期preflight抓到`UNTRACKED_SOURCE_REJECTED`：公开v0.3输出被错误纳入clean source与Git状态摘要，形成“发布改变源码权威”的自引用。
- 新增真实临时Git攻击回归；仅精确排除`artifacts/f1-platform-shell/v0.3/**`的tracked/untracked文件及status pathspec，绝不把它加入源码allowlist。v0.2撤销证据、其他artifact与普通未跟踪文件仍改变摘要或被拒绝。
- 红灯为新批次触发`UNTRACKED_SOURCE_REJECTED`；修复后旧tracked顶层切换为immutable batch/current时source SHA、repository-state SHA完全一致，clean checkout不含v0.3；`tests.test_f111_clean_rebuild`=`Ran 49 / OK`。
- 不启动服务的真实Docker/Compose早期链路通过：有效Compose服务数=17、精确cleanup残留=0；说明`COMPOSE_CONFIG_RED`与来源自引用两项均已闭合，下一步重跑完整离线定向后才允许formal第2轮。
- 完整20模块离线定向门已重跑：`Ran 446 / OK`、skipped=0，`git diff --check`=0；主工作树仍clean。满足第2轮formal启动前置，尚未据此写READY。
- 第2轮formal在primary prepare期诚实REJECTED，固定blocker=`F0G_SOURCE_AGGREGATE_RED`、gate_count=0；未进入业务门、无READY。
- 单变量只读探针确认：clean以`input_bytes`向`docker exec`送固定SQL却漏`-i`，Docker exit=0/空输出，严格parser随后拒绝；同源`--command`及stdin+`-i`均为单行可解析。F0I同路径会在下一步同样失败，因此一并修复。
- `_process`新增双向stdin契约：Docker exec有input必须首项`-i/--interactive`，无input不得开interactive，任何TTY组合拒绝；F0G/F0I聚合均显式`-i`与`--no-password`。
- 新增2项结构/调用攻击测试，红=`6 failures`，绿后clean模块=`Ran 51 / OK`；真实只读源复核F0G/F0I aggregate parse均=1、cleanup残留=0。下一步完整定向重跑后才允许formal第3轮。
- 完整20模块定向再次重跑=`Ran 448 / OK`、skipped=0；本轮未删/skip旧测试，满足formal第3轮前置。若第3轮仍失败，将按任务书暂停直接重试并切换为分段真实栈探针定位，不重复消耗完整验收。
- 第3轮formal按设计REJECTED，固定blocker=`F0G_TEMPLATE_MIGRATION_RED`、gate_count=0；依三连败规则停止直接重跑，改为仅运行至F0G模板的分段真实栈探针。
- 模板迁移前置补齐：F0G迁移DSN显式升级`postgresql+psycopg`（镜像仅锁psycopg3），空模板库先撤PUBLIC、最小grant并创建owner=f0d_migration的`f0d` schema。红=1 error+1 failure，绿后clean=`Ran 53 / OK`。
- 分段探针证明F0G migration已通过，下一固定红灯为`F0G_TEMPLATE_RESTORE_RED`，cleanup残留=0；因此第3轮blocker已经真实闭合且未直接重跑formal。
- 只读源聚合确认F0F page evidence=249且其所需F0E local OCR run=24；原F0G dump只含F0D/F0F，恢复时必然缺外键父行。scope/data-dump合同升级v2并固定F0D+F0E+F0F三schema，parser同时要求F0E与F0F均有非空数据。
- 新合同聚焦先红（scope schemas不符、F0E COPY被拒），修复后prepare+clean=`Ran 101 / OK`、`py_compile`与`git diff --check`绿。旧v1私有输入包已失效，需可恢复迁出后由固定prepare入口重建v2包，再复跑同一分段探针。

## 2026-08-10 用户优先级切换：暂停发布验收

- `F1_1_1_PAUSED_NOT_ACCEPTED`：用户明确将优先级切换为可见业务功能开发；本阶段固定停在F0D+F0E+F0F v2合同与prepare+clean定向`Ran 101 / OK`，不据此声明验收通过。
- 停止重建私有输入包及模板恢复探针；不再运行formal、reverse、SBOM、双轮clean rebuild、全仓回归或M4。
- 既有源码修改、不可变REJECTED批次与诊断记录原样保留；不删除或改写证据，不清理共享数据库、对象、卷或历史材料。
- 当前隔离worktree继续作为P2开发基础，避免未提交F1.1.1修复丢失；仍不commit、不push、不部署。
- 原30分钟自动化已改写为P2连续开发合同，明确禁止心跳恢复F1.1.1发布验收。
