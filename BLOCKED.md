# BLOCKED

> **历史阻塞与事故台账。** 当前未验证边界和待决事项统一见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文早期“无阻塞/完成”只描述当时快照，不代表 F1.1.1 已通过现役发布验收。

- F1.1.1 全部完成（2026-08-09）：M1 安全边界（f1_0003）→ M2 真实幂等/恢复/HTTP-only E2E（reverse 17项全0 exit0）→ M3 干净重建/日志/不可伪造产物（v0.3 READY，v0.2 revoked）。全仓 126 tests OK，静态 850，clean-rebuild 验证通过。无阻塞。
- F1.1.1 M2 完成（2026-08-09）：真实幂等/恢复/HTTP-only E2E；`f111_reverse_verify.py` 17项全0 exit 0；`test_f111_recovery_idempotency` 6项OK；F1 40 + F1.1 49 + F1.1.1 14 + M2 6 = 109 全绿。无阻塞。新reverse已取代旧脚本（旧脚本直调内部函数插业务行）。
- F1.1.1 M1 完成（2026-08-09）：f1_0003 + 运行时修复，`test_f111_security_boundaries` 14项红→绿（Ran 14 OK），F1 40 + F1.1 49 + F1.1.1 14 = 103 全绿。无阻塞。旧reverse按任务书M2重写（现 `tenant_crosswires=1` 因audit对enterprise_admin正确403、`orphan_objects=6` 因旧reverse自插业务行，均留M2随机run_id自清reverse处理）。
- F1.1.1 Task0 基线核对（2026-08-09）：全部结构项一致（git 仅本书、head=f0d_0006/f1_0002、15服务healthy、49/OK、静态813）；旧reverse真实 exit2（非假绿）与任务书一致；唯一数量差=orphan_objects 实测2 vs 任务书记载1。只读核对：MinIO对象7/document行192/upload_task142/outbox209，orphan键为UUID形36位、非reverse的e2e-<uuid>.pdf形，判定非本轮运行新增、为F1.1历史残留，exit=2方向一致、不改变任务书结论；未清孤儿。开工回执已写入PROGRESS.md。
- 外部安全前置项：旧 Demo `.env.local` 中的 API Key 需要由账号责任人轮换；本目标不读取、不打印、不修改该文件，也不依赖该 Key，因此不阻塞本地 Fixture 校验器实施。
- F0-C 无新增实施阻塞。边界仍为本地 Fixture 工程候选计划：未执行 OCR、未建立 Gold、未证明解析准确率，也未开放外部处理或公开展示。
- F0-D 无实施阻塞。最终验收运行目标是 `f0d_acceptance_v03` + `/private/tmp/anhuan-f0d-acceptance-v03`；默认旧 `f0d`、v01 和 v02 仅保留为历史环境，未被覆盖，不代表当前底座。真实客户、地区/行业、Acceptance Gold、外部 OCR/LLM、专业责任人、客户 UAT 与生产授权均仍未确认，已作为默认关闭且无开闸 API 的 P0 门禁交付。
- F0-E 无实施阻塞。最终验收运行目标是新建的 `f0e_acceptance_v01` 与对应 fresh 本地 vault；默认 `f0d`、F0-D v01/v02/v03 数据库和 vault 均未修改或删除。
- 非阻塞工程边界：GUI OpenCV wheel 的离线导入缺少 `libxcb.so.1`，已换为同版本且 hash 固定的 headless wheel；许可证与模型清单仅为工程盘点，不是法律、客户或生产授权。
- 真实客户、地区/行业、Acceptance Gold、外部 OCR/LLM、专业责任、客户 UAT 与生产仍为 CLOSED；本轮只证明本地 Fixture 的 OCR 执行证据，不证明识别准确、Gold、SEARCH_READY 或专业结论。
- F0-F 非阻塞安全记录：为定位一次被合成 FIFO 阻塞的测试进程，进程列表诊断输出夹带了另一本地应用的命令行认证参数；未写入工作区、数据库或 artifact，且与 F0-F key、Fixture 正文和客户资料无关。建议对应本地应用重启会话或轮换该临时认证参数；F0-F 后续诊断只使用精确 PID 查询，不再输出全进程命令行。
- F0-F 任务书冲突（已解除）：主执行者与独立审计曾确认 F0-E/F0-F 对同一全局 `head` 分别要求0003/0004而互斥。领导随后明确授权阶段隔离例外；仅把旧 F0-E fixture 的 upgrade target 固定为 `f0d_0003`，保留其原断言，F0-F 仍使用全局 head=`f0d_0004`。F0-E 数据库测试11/11通过、全仓357/357通过；84文件新冻结组指纹已登记为 `73d36ea5c2ecf95c78636b4e1a5c70c9e596c1ad5ba601460dfb396e37a27c38`。未使用条件 revision、stamp、trigger/view、双 version 行或改断言等伪造方案。
- F0-F 非阻塞流程记录：一次仅用于“无匹配即通过”的静态 `rg` 卫生扫描误加了 `|| true`；该输出未作为验收证据、未写业务数据，随后立即用完全相同 pattern 去掉 `|| true` 重跑并得到真实 `exit=1`（无命中）。最终交付只采用后一次原始退出码；不以该误命令宣称假绿。
- F0-F 收口流程事故：为定位两份冻结 source manifest，主执行者使用了范围过宽的只读 `sed/find` 诊断，工具输出带出了 manifest 内登记的源文件名。未输出正文、密钥、电话或邮箱，未写入新工作区文件、数据库或 artifact，但既有聊天工具输出无法撤回，故不能宣称本轮全过程“源文件名未进聊天”。随后停止该读取方式，余下原件校验仅使用 `shasum -s -c` 的静默退出码和聚合计数。
- F0-G 阶段隔离阻塞：新增合法线性 `f0d_0005` 后，冻结旧 F0-F fixture 仍执行 `upgrade head`，但其测试语义固定在0004；0005又必须撤销旧 runtime 对正文表和直接解密函数的权限，才能满足“正文只向 assigned actor 返回”。实际全仓红灯为：`Running upgrade f0d_0004 -> f0d_0005`，随后 `ERROR: setUpClass (test_f0f_controlled_body_gold.F0FDatabaseMigrationTests)`，在旧 crosswire 探针的 runtime `body_state()` 直读处得到 `DatabaseError: DATABASE_TRANSACTION_FAILED`；汇总 `Ran 325 tests in 42.742s / FAILED (errors=1)`。白名单不允许改旧测试；分支、多 version 行、按库名跳迁移、伪造 stamp 或放回旧直读权限都违反证据正确性。其余 F0-G 项继续；唯一干净收口需要单独授权把旧 F0-F fixture 的 Alembic target 固定为 `f0d_0004` 并重登记冻结指纹。
- F0-G 非阻塞流程记录：服务子任务一次 forbidden-import 静态诊断、主线程一次“文件可能尚不存在”的只读诊断误用了 `|| true`；两次都不是被测验收、未写业务数据，也未据此宣称通过。服务同 pattern 已去掉 `|| true` 重跑得到原始 exit=1/零命中；主线程后续不再使用该写法，最终证据只采用真实退出码。
- F0-G 阶段隔离阻塞最终复核：全部F0-G深审修复后，全仓真实回归为`Ran 460 tests in 49.056s / FAILED (errors=1)`、skipped=0；仍只有`test_f0f_controlled_body_gold.F0FDatabaseMigrationTests.setUpClass`这一项。调用链为旧fixture执行`upgrade head`到0005后，`probe_initial_crosswire -> body_state -> tenant_transaction`得到脱敏`DatabaseError: DATABASE_TRANSACTION_FAILED`；这是0005按任务书撤销runtime正文表直读后的预期阶段隔离结果，不是新F0-G API失败。当前白名单内无诚实修复；下一轮需要领导明确授权：仅把旧`tests/test_f0f_controlled_body_gold.py` fixture的Alembic upgrade target从`head`固定为`f0d_0004`，保留其阶段断言，并按原算法重新登记F0-A～F冻结指纹。未获该授权前不得修改旧F0-F测试、放回直读权限或宣称全仓全绿。
- F0-G 收口后阻塞复核（2026-08-06）：固定loopback serve、完整ACL catalog、三角色assignment绑定、4096-byte裁决上限、15正文片段/Base64URL/受控日志reverse、准确SBOM及新增24项测试均已收口；F0-G=`159/159 OK`、reverse严格11行全绿、loader=`516`。全仓=`Ran 484 / errors=1`且仍只有同一个旧F0-F `head→0005` setUpClass错误，恰有32项未展开。唯一越界动作仍需领导明确授权：只把该旧fixture升级目标固定为`f0d_0004`并重新登记冻结指纹；本轮未修改旧F0-F。
- F0-G 收口流程事故（2026-08-06）：主线程首次复核两份source manifest时错误地以manifest所在子目录为工作目录，`shasum -s -c`因相对路径找不到文件而把26个登记源文件名写到工具stderr/聊天。未输出正文、key、token、电话或邮箱，未修改原件/DB/artifact，但聊天输出不可撤回，因此本轮不能宣称“源文件名从未进聊天”。后续改为在只读`fixtures`根目录静默校验，仅记录退出码与聚合计数。
- F0-G 最终阻塞判定（2026-08-06）：收口后的最终实跑仍为`loader=516 / Ran 484 tests in 52.571s / FAILED (errors=1)`，唯一错误与此前两轮相同，旧F0-F数据库类的32项因`head→f0d_0005`后阶段直读被拒而未展开；F0-G自身159项、reverse 11项、loopback bind、产物重生、冻结与卫生检查均通过。该阻塞已连续超过3轮且白名单内无证据正确的修法，故停止继续尝试；只有领导明确授权修改旧F0-F fixture目标为`f0d_0004`并重登记冻结指纹后才能闭环。
- F0-G 阶段隔离阻塞已解除（2026-08-06）：领导已明确授权唯一例外；旧F0-F fixture现固定升级到`f0d_0004`且原断言保留，新测试SHA与112文件冻结组已实算登记。该项不再阻塞，等待定向、reverse与全仓验收确认。
- F0-G 非阻塞历史残留（2026-08-06）：最终只读盘点发现13个严格`f0f_test_<16hex>`命名、活动连接为0的数据库，形态与此前旧F0-F `setUpClass`失败留下的scratch库一致，但数据库目录没有足够证据把每个对象精确归属到某一轮。任务书禁止修改/删除旧DB，因此本轮未删除；F0-G自身`f0g_test_`/`f0g_verify_`临时数据库和token/log残留均为0。
- F0-H Task 0 非阻塞流程事故（2026-08-06）：首次执行26份原件的 `shasum -s -c` 时误把 `fixtures` 当登记根，命令失败并将manifest内26个登记文件名写入工具stderr/聊天；未读取或输出正文、密钥、电话、邮箱，未修改原件、DB、镜像或artifact。随后改用不回显路径的只读SHA脚本，在唯一登记根得到 `files=26 failures=0`；后续只记录聚合计数，不再用错误工作目录运行manifest。
- F0-H 非阻塞红→绿记录（2026-08-06）：首次真实 v6 PDF 以脱敏 `OCR_RESULT_INVALID` 退出2，原因为 RapidOCR v3 bbox ndarray 与旧 list/tuple 合同不兼容；显式坐标适配、离线重建并重冻结后 exit0。随后合成 JPEG 暴露宿主 `.venv` 无 Pillow；未新增宿主依赖，改为标准库固定字节后 PDF/JPEG/blank 全绿。
- F0-H 非阻塞诊断事故（2026-08-06）：在 CLI 尚未完成统一错误转换时，两次由开发者直接调用内部 artifacts 函数，Python traceback 将本地源码绝对路径写入工具输出；未含原件正文、源文件名、电话邮箱或密钥，未写 artifact/DB/旧证据，但聊天输出不可撤回。最终 CLI 只输出固定脱敏失败 JSON，产物卫生不据此宣称“开发过程绝对路径从未进聊天”。
- F0-H 非阻塞并发流程记录（2026-08-06）：主线程第一次 full 期间，产物子任务误判前置完成并启动自身 smoke/full，短暂违反“单容器串行”的验收编排；主线程立即中断子任务，确认后台进程自然结束、F0-H容器残留0，并废弃该轮作为验收证据。随后从零残留状态重新串行跑两次 full，二者安全摘要完全一致；最终证据只采用后两轮。
- F0-H 反向验证红→绿（2026-08-06）：第一次严格reverse为`0,2,2,0,0,0,0,1,0,0`/exit2；未发生下载，红项准确指出 Dockerfile 仅以环境变量禁止索引，缺显式pip参数。补 `--no-index`、断网重建及重冻结后，同一脚本为`0,2,2,0,0,0,0,0,0,0`/exit0；未放宽判据或改期望值。
- F0-I Task 0 非阻塞流程事故（2026-08-06）：为定位旧F0-G本机数据库常量，一次只读 `sed` 工具输出带出了源码中已固定的本机 Fixture bootstrap DSN口令。未包含26份资料正文、F0-F/F0-I key、token或生产凭据，未写入artifact/日志/数据库；但聊天输出不可撤回。后续仅从模块内导入并输出布尔聚合，不再打印DSN。
- F0-I Task 1 非阻塞流程事故（2026-08-06）：接口勘察时主线程再次把含固定本机 Fixture DSN默认值的旧 `__main__.py` 纳入只读批量输出，重复暴露同一组非生产bootstrap口令。未触碰原件正文、新旧key/token、数据库或artifact；后续停止输出任何DSN定义文件，数据库配置只由既有模块构造且验收只显示脱敏计数。此项不作为“零聊天凭据暴露”的通过证据。
- F0-I Task 2 非阻塞流程记录（2026-08-06）：测试子任务一次仅用于“零禁词命中”的静态 `rg` 诊断误加 `|| true`；未作为测试或验收通过证据、未写数据库/原件/artifact。后续同类扫描均保留 `rg` 原始退出码，最终交付不引用该次结果。
- F0-I Task 2 非阻塞诊断事故（2026-08-06）：主线程为只读提取F0-I模块接口，错误使用了会反解完整函数体的AST命令，工具输出再次展示本机Fixture bootstrap口令的可推导构造逻辑。未输出26份正文、源文件名、新旧key/token或生产凭据，未写artifact/数据库/原件；但聊天输出不可撤回，最终不得宣称“开发过程无本机Fixture凭据构造信息进入聊天”。后续只用定向行号读取非配置模块，不再输出config/bootstrap定义。
- F0-I Task 2 非阻塞诊断事故补充（2026-08-06）：主线程为增加XLSX公式合同测试读取F0-I测试文件开头，工具输出包含该测试内部已有的三组本机scratch角色DSN固定口令。它们仅用于loopback Fixture PostgreSQL，不是生产凭据；未包含正文、源名、key/token，未写artifact/DB/原件，但输出不可撤回。后续不再展示任何测试/配置文件的DSN构造段，最终安全结论仅针对产物、日志、数据库明文列与正文/key泄漏。
# F0-I 首次 artifact 签发失败（2026-08-06，已解决）

- 原始公开输出（逐字）：`{"error":"F0I_ERROR","reason_code":"ARTIFACT_GENERATION_FAILED"}`；进程exit=2。
- 失败前真实smoke→full→full及reverse已各自通过；失败后只读确认`artifact_dir=0/files=0`，没有半成品。
- 根因：`status.html`文案含公开载荷守卫明确禁止的敏感存储字段名；原测试只查远程资源，未把真实HTML送入完整guard。改为不暴露列名的“pgcrypto-encrypted values”，新增真实status payload guard回归后定向122项全绿。
- 终审另发现初版SBOM误写PostgreSQL 16；真实实例为18.3、pgcrypto 1.4。新增live component对照时首轮测试因遍历无version的模型组件得到`KeyError: version`，只筛数据库组件后123项全绿；最终两轮产物SHA一致，未放宽公开载荷或SBOM判据。

# F0-I 首次真实 full 失败（2026-08-06，已解决）

- 原始公开输出（逐字）：`{"error":"F0I_ERROR","reason_code":"REPLAY_MISMATCH"}`；进程exit=2，用时约9.4秒。
- 前一项真实smoke已成功，聚合为10 scopes、110 visual、5 OCR；本次失败后未重跑full、未清库、未删key、未执行任何恢复性写入。
- 根因已只读确定：登记smoke的10份资料虽是full的严格子集，但不是full前10份；两集合只重合4份。旧实现以`full sources[:10]`冒充smoke集合，在文档构建/OCR/持久化之前必然拒绝。
- 已用登记smoke plan在同一只读事务解析并绑定真实10份source version，要求10个唯一版本且为full严格子集；新增“subset但非prefix”回归。定向套件由120增至121并全绿。
- 失败后带租户上下文的只读聚合=`configuration1/run1/scope10/page110/block1022/chunk264/link738`，`smoke_runs1/full_runs0/smoke_ocr5/full_ocr0`；证明本次失败未执行full OCR、未写full数据，既有smoke完整保留。
- 一次诊断脚本误把字典行当序号访问，公开输出仅`KeyError: 0` traceback（无DSN/正文/路径/文件名/PII）；修正为命名列后得到上述只读聚合，不作为验收绿灯。
- 一次并行只读审计未设置tenant context，在FORCE RLS下得到七表假零；该结果立即作废，认证上下文复核得到完整smoke聚合。修复后full只补19个OCR页，第二次full为零新增/零OCR。

# F0-I 非阻塞共享工作区记录（2026-08-06）

- 终审发现根级`后续开发路线_成熟方案优先_2026-08-06.md`的birth time位于F0-I实现期间，且不在本任务白名单；当前线程与只读审计agent均未创建/修改它，向既有agent核实也未能证明作者。按共享工作区规则把它视为未归属的并发文件，保持原样且不纳入F0-I交付/冻结/验收证据；未擅自删除或修改。
- 最终全仓命令的旧FastAPI/Starlette依赖发出一条deprecation warning，stderr带`.venv`中的本机绝对路径；它不是F0-I artifact/持久日志，未含26份正文、源文件名、key、DSN或PII，但聊天输出不可撤回。因此最终只证明F0-I CLI/三产物/数据库的正文与路径泄漏为0，不宣称整个开发聊天从未出现本机路径。
- 独立DB补充审计首轮把真实列名误写为不存在的短名并只返回`DatabaseError`类型；没有输出SQL详情/正文/凭据，也没有写入。改用登记列后，18个复合外键、249个唯一processing unit和3个密文字段聚合均全绿。

# F0-J0 硬条件失败：RAGFlow v0.26.4 依赖外部 embedding 服务（2026-08-07）

- arm64 官方构建（download_deps.py → ragflow_deps → 主 Dockerfile，xgboost 1.6.0、unixODBC/msodbcsql18、默认 Elasticsearch、未用 Infinity）**第 1 次即成功**；完整日志 `infra/f0j0/ragflow-build-attempt-1.log`。5 容器栈端口全绑 127.0.0.1。
- 公开输出（逐字，已脱敏）：`{"code":100,"message":"LookupError('Provider  not found for model .')"}`。add_chunk 与 `/retrieval` 均无条件要求 embedding 模型；`default embedding config` 为 `{'model':'','factory':'','api_key':'xxx','base_url':'http://:80'}`。
- 证据正确性：arm64 镜像内无本地内置 embedding 模型（仅 parser 模型 det/layout/rec/tsr ONNX + xgb；无 torch/transformers/sentence-transformers）；全部 embedding factory 需外部 API key 或 TEI/HTTP 端点。按任务书预授权降级分支收口：本轮以 OpenSearch 全量结果 + RAGFlow arm64 硬条件失败证据交付，不算任务失败，未中途请示。
- 已确认的操作细节：注册/登录/JWT API key/建 dataset/建 empty document 均可经官方 REST API 完成（无需 Web UI 手工步骤）；探针数据集已全部清理。

# F0-J0 流程记录与事故登记（2026-08-07）

- **非阻塞凭据处置记录**：探针本地 RAGFlow 用户（`f0j0probe@example.com`，合成口令）的 access_token 与 JWT API key 在初始化调试过程中短暂出现在工具输出中；二者仅绑定该探针本地用户、仅用于 RAGFlow 栈，随 `/private/tmp/anhuan-f0j0-secrets` 删除及 RAGFlow 栈/卷拆除已不可复现。聊天输出不可撤回，故不宣称"开发过程零凭据进聊天"；无生产/仓库/F0-I 库影响。
- **非阻塞默认口令说明**：`infra/f0j0/.env` 与 compose 内 `infini_rag_flow` 等为 RAGFlow 官方 .env 模板的文档化默认值（非本任务生成 secret），用于 loopback 探针栈；栈已拆除，无残留。密钥类仅 `openssl` 生成的管理口令写入 `/private/tmp/anhuan-f0j0-secrets/`（0700/0600），已删除。
- **无任务级事故**：四任务（基线/OpenSearch/RAGFlow/拆除/选型）均按回执完成；无伪造、无假绿、无 `|| true` 用于证据；探针零残留、F0-I 三产物与 26 原件零漂移；未打开任何 closed gate；未宣称检索质量、准确率或生产可用。RAGFlow 硬条件失败为任务书预授权降级，非任务失败。

# F0-J0 RAGFlow embedding 硬条件已解除（2026-08-07 领导授权）

- 领导提供火山引擎 Ark `doubao-embedding-vision` 模型及 Base URL/API key，并授权本轮数据外发 Ark 做 embedding（覆盖 D06「仅本机/隔离」与任务书「零外部 API key」两条约束）。
- 实测全链路 code=0：建 dataset（`doubao-embedding-vision@VolcEngine`）→ 建文档 → add_chunk → /retrieval 命中返回。此前 `LookupError('Provider  not found for model .')` 硬条件已解除。
- 范围仅「能建库+能导 chunk+能检索」；完整 C1-C12 机制留 F0-J1。凭据仅存 `/private/tmp/anhuan-f0j0-secrets/`（0700/0600），探针结束随容器删除；未写入任何产物/聊天重复。

# F0-J1 前置阻塞：两个 API key 缺失（2026-08-07）

- 任务书任务0第6/7条要求 `/private/tmp/anhuan-f0j1-secrets/ark_api_key` 与 `deepseek_api_key` 均存在且 0600，缺失 → 停。当前两者均缺失（secrets 目录尚未创建）。
- 已完成的只读闸门全部通过：F0-I 三产物 SHA 冻结值一致；F0-J0 两产物 SHA 已复核登记（json=`35574443…`/md=`726cf0f3…`）；docker `anhuan-f0j0-`/`anhuan-f0j1-` 残留均 0；`anhuan-f0d-postgres-1` healthy；f0i key 600/32 bytes；七表==BASELINE（child=300）；alembic=`f0d_0006`；全仓 `Ran 690 tests / OK (skipped=2)`。
- 停止原因：等待领导提供 Ark API key（火山引擎 `doubao-embedding-vision`）与 DeepSeek API key（默认模型 `deepseek-v4-flash`）；提供后放 `/private/tmp/anhuan-f0j1-secrets/`（0700/0600），聊天不出现。

# F0-J1 流程记录与机制差异登记（2026-08-07）

- **机制差异（非事故）**：RAGFlow `add_chunk` API 拒绝空正文（2 个空 child chunk 不入索引，298=300−2）；DELETE chunks 成功返回 `{code:0}` 不报删除数（用真实计数差验证）；list_chunks `page_size<=100`；dataset 级 `chunk_count` 为本地计数器可漂移（用逐文档真实计数）；ES `terms` aggregation 对 tag_kwd 高基数 bucket 截断（用分页全量核对）。
- **环境问题（已修）**：本机 Python 缺 CA 证书致 DeepSeek HTTPS 失败，改用 certifi bundle；RAGFlow 检索主路径不返回 tag_kwd，检索命中经 chunk-detail API 解 canonical chunk_id。
- **LLM 行为记录**：`deepseek-v4-flash` 长 prompt 下偶发空回或不内联引用，用分层重试（complete 4 次 × QA 3 次）缓解；"无法确认"回答判合规拒答（LLM_UNABLE_TO_CONFIRM）。
- **无任务级事故**：F0-J1 任务0~6 均按回执完成；探针零残留、F0-I/26 原件/PG 零漂移；未开闸门；未宣称准确率/生产可用。

# F1 端口冲突处置（2026-08-08）

- F1 任务书闸门2期望 `^anhuan-f0(j0|j1|f1)-` 容器为空，但 F0-J1 栈按旧约定保留运行中。
- 端口冲突：F0-J1 的 MinIO 占 9000/9001、valkey 占 6379，与 F1 的 MinIO/Redis 端口要求冲突。
- 处置：F0-J1 阶段已完成且产物冻结，停掉 F0-J1 栈释放端口给 F1（镜像与 artifacts 保留，栈可随时重建）；F0-J1 secrets 保留。此决策按"拿不准写 BLOCKED"登记，非阻塞。

# F1 Keycloak 25.0.6 realm 创建缺陷（2026-08-08，处置中）

- 症状：通过 admin REST API 创建的 `anhuan` realm，其任何用户（含 emailVerified=True、requiredActions=[]、密码已设）在 password grant 登录均报 `{"error":"invalid_grant","error_description":"Account is not fully set up"}`（error=resolve_required_actions）。
- 已排除：用户字段（与 master 成功用户逐字段一致）、realm 常规配置（与 master diff 仅 realm 特有字段）、credential createdDate、客户端 direct access grants、firstName/lastName、username 含 @。
- 对照：master realm（Keycloak 内置创建）同样流程登录成功；API 创建的 realm 均失败（含最小重建 realm）。DEBUG 日志显示 Hibernate 在 resolve_required_actions 阶段查询 UserEntity.requiredActions 关联时失败。
- 结论：Keycloak 25.0.6 通过 `POST /admin/realms` 创建的 realm 认证结构初始化不完整（内置创建的 master 正常）。
- 处置：改用官方 `kc.sh import` 在启动时导入完整 realm JSON（含 roles/clients/users），绕开 API 建 realm 路径。

# F1 Keycloak 硬条件失败（2026-08-08，第25+轮排查结论）

- 症状：Keycloak 25.0.6 与 26.0.8（H2 开发模式）下，**任何非 master realm 的用户**（API/kcadm/import-realm 创建，含内嵌凭据）在 password grant 登录均报 `{"error":"invalid_grant","error_description":"Account is not fully set up"}`（error=resolve_required_actions）。
- 已穷尽排除：用户字段（与 master 成功用户逐字段一致）、realm 常规配置（逐字段 diff 仅 realm 特有项）、authenticationFlows（官方 kc.sh export 完整 18 flows + authenticatorConfig 导入）、credential createdDate、客户端 direct access grants、默认角色、firstName/lastName、两个 Keycloak 版本。
- 对照：master realm（Keycloak 内置 bootstrap）登录正常；REST/kcadm 在 master 创建的用户曾成功，重启后密码失效（H2 数据问题）。
- 结论：本环境 Keycloak 实例在 H2 模式下非 master realm 认证结构损坏（DEBUG 日志显示 resolve_required_actions 阶段 Hibernate 查询 requiredActions 关联异常）。非我方配置问题。
- 处置：下一步尝试 PostgreSQL 后端（任务书本要求生产前迁移 PG）；若仍失败，向领导报告 Keycloak 硬条件失败并建议替换（如自托管 OIDC 替代或升级路径）。

# F1 Keycloak 硬条件失败已解除（2026-08-08）

- 根因定位：手工/API 创建的 realm 登录报 "Account is not fully set up"，是因为 realm JSON 缺少 master 完整骨架中的 `clientScopes`（13 个）+ `components` 等结构，导致认证流程 profile 步骤无法完成。
- 解法：以官方 `kc.sh export` 导出的 master-realm.json 为骨架，仅替换 realm 名/角色/客户端/用户，保留完整 authenticationFlows/authenticatorConfig/clientScopes/components 后 `--import-realm` 导入。
- 验证：tester（partner+auditor）与 admin@anhuan.local（super_admin）password grant 登录均 OK；`tests/test_f1_auth.py` 5 项全过。
- 版本记录：25.0.6/26.0.8/26.1.1 中 26.1.1 为当前使用版本（env 密码注入正常）。原"硬条件失败"记录作废。

# F1 流程记录与处置汇总（2026-08-08）

- Keycloak 25.0.6/26.0.8 非 master realm 登录缺陷（"Account is not fully set up"）→ 30+ 轮排查 → 26.1.1 + master 完整骨架（含 clientScopes/components/authenticationFlows）import 解决；root cause=手工 realm JSON 缺骨架结构。
- alembic f1 分支 version 表覆盖 f0d 冻结 head → `infra/f1/migrate_f1.py` 独立 version 表分离（f0d=f0d_0006、f1=f1_0001）。
- SQLAlchemy Core select 返回 Row 致 `UUID object has no attribute id` → 全 routers 改 ORM select；asyncpg pgproto.UUID → 改 psycopg3 async。
- 端口：8000 被 Docker 占用 → FastAPI 用 8001（任务书矩阵 8000，记差异）；otel-collector 接收端内部化、Jaeger OTLP 绑 0.0.0.0。
- OTel 导出被宿主 HTTP 代理拦截 → 清空代理 env；ResourceWarning（psycopg 连接未显式关闭，非失败，记入待办）。
- 待办结转：invitation API 端点/前端邀请页、上传任务接 PostgreSQL f1.upload_task 表、RAGFlow 索引接线（QA 端点 CHAIN_NOT_WIRED 占位）。
- 无任务级事故：F0-I/26 原件/PG 零漂移；未开闸门；未宣称生产可用。

# F1.1 阻塞：Ark embedding API key 失效（2026-08-08）

- 直连 Ark `https://ark.cn-beijing.volces.com/api/v3/embeddings` 与 `/embeddings/multimodal`（`doubao-embedding-vision`）均返回 `401 AuthenticationError: The API key or AK/SK in the request is missing or invalid`。
- key 文件 `/private/tmp/anhuan-f0j1-secrets/ark_api_key`（46字节、纯ASCII、无换行/空格）与 F0-J1 时一致；DeepSeek key（同目录）直连 200，证明是同目录内 Ark key 单独失效（可能被轮换/过期）。
- 影响：RAGFlow VolcEngine provider/ark-probe 实例无法配置 → 每企业 dataset 无法创建 → 登记Fixture chunk 无法入 RAGFlow → QA 检索链无法接通（F1.1 E2E 的「索引/QA」两环被阻断）。
- 处置：F1.1 RAGFlow sidecar（mysql/es/minio/redis/ragflow）已起、API key 已注册（AUTH_API token），provider 配置与索引/QA 代码已就绪；仅差有效 Ark key。等待领导提供新 Ark key 后，`ragflow_provision.ensure()` 一键补齐，E2E 可跑。
- 非阻塞继续项：DB任务/outbox/登记SHA门禁/租户隔离/QA持久化加密/Compose/Web/测试/反向验证脚本已持续推进。
- 附注：`/private/tmp/anhuan-f0j1-secrets/ragflow_api_key` 已被 F1 RAGFlow 实例的 AUTH_API token 覆盖（F0-J1 栈已停且实例独立，若未来重启 F0-J1 需重新获取其 API key；已登记此流程差异）。

# F1.1 阻塞解除：Ark embedding key 已更新（2026-08-08）

- 领导提供新 Ark key（`ark-f13cf5fe-…-f4723`）与正确 Base URL `https://ark.cn-beijing.volces.com/api/plan/v3`、模型 `doubao-embedding-vision`（向量模型不支持 Auto/控制台切换）。
- 原 key 确认失效（直连 401，与 F0-J1 MySQL `tenant_model_instance` 记录一致，确认为 key 本身被轮换/过期，非格式问题）。
- 更新后：VolcEngine provider + ark-probe 实例配置成功（model_type 需为 list、实例名不可为 default、单活动实例 fallback 解析）、企业 A/B dataset 创建成功、F0-I chunk 写入 RAGFlow 成功（65 chunks/登记SHA）、QA 链返回带 citation 答案（6 citations）、跨租户 404、反向验证 `valid_e2e_exit=0` 全绿。
- 附注：RAGFlow add_chunk 对相同 content 幂等（重复投递不重复计块）；`ragflow_api_key` 已覆盖为 F1 实例 AUTH_API token（F0-J1 栈停，若重启需重取，已登记流程差异）。
