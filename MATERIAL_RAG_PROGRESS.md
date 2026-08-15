# MATERIAL RAG Progress

## 2026-08-15｜本切片目标（最多10行）

- 目标：fd142 门禁解除后，两轮内拿到 `LOCAL_MATERIAL_RAG_VERIFY_OK`，或留下可定位唯一主因的固定证据。
- 任务0：cwd/分支/残留0/无并发通过；`anhuan-f1-ragflow-1` 原为 running/healthy。指定 `/private/tmp/f1lockvenv2` 全量 unittest 为 Ran 40 / errors=9（venv 仅有 pyc、0 个 `.py`，缺可用 `psycopg`/`jose` 源码，未装依赖）。既有项目 venv 复核 `Ran 40 tests / OK`，errors=failures=skipped=0。
- 两次 `material-rag-verify` 均先只停共享 `anhuan-f1-ragflow-1`，finally 残留0并恢复该容器 running/healthy；其他共享容器状态未变。额度 2/2。
- 第1次约 248s，exit 2，`LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED`；验证器已到达（exit 1）；无 `PROVIDER_EVIDENCE`、无预览 `error_reason`。cleanup 前：clamd `running/starting/restart_count=1/oom=false`；专属 ragflow/ocr healthy。
- 按该快照做最小修复：预检 60s 内重试 `P3_SCAN_PROTOCOL_ERROR`；clamd healthcheck `retries` 3→60。直接检查 2 项 OK。
- 第2次约 249s，同一固定码与同一 clamd 快照。按授权停止，不再重放。不能记 `VERIFY_OK` 或 `SMOKE_PASSED`。

## 2026-08-14｜当前结论

- 最新明确授权的诊断性完整 `./scripts/localctl material-rag-verify` 约 8.1 分钟后固定停在 `LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED`（exit 2）。cleanup 前留下服务快照：OCR `exited/137/unhealthy`；RAGFlow `oom_killed=true` 但仍 running/healthy，未再被提升为 OOM 主因。验证器未启动，故无 `VERIFIER_REASON` 与预览 `error_reason`。不能记 `LOCAL_MATERIAL_RAG_VERIFY_OK` 或 `SMOKE_PASSED`。
- 失败后补了未再跑 verify 的诊断收口：验证器未到达时，cleanup 前会转印 `VERIFIER_REASON=NOT_REACHED` 与预览 `error_reason=NOT_REACHED`。该改动尚未经下一次完整重放证实。
- PDF `/AA` 离线四份固定材料仍为 `OK:49/5/65/17`，不能证明整栈预览已过。
- 专属 container/volume/network 残留为 0。工作树未暂存、未提交、未推送。再次跑完整 verify 必须取得新的字面授权。

当前状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。

## 历史实施与重放轨迹

以下记录按发生时间保留；其中“当前”“下一次”“唯一”等表述只代表该条记录形成时的现场，不覆盖上方当前结论。

- 基线：`codex/material-intake@aef1a66202f42af028196e57ef915b8b3c9d4040`，新分支 `codex/material-rag`。
- 目标：将 `service_provider/client` 逻辑知识域接入独立物理 RAG，并保持客户隔离和页码证据。
- 顺序：真实四 PDF 身份门 → f1_0015/canonical unit/job → scope 索引与检索 → 专属验证。
- 外部边界：仅允许验证器登记且通过本地敏感过滤的 Demo canonical 文本、固定 provider/client canary 和验证查询文本，经正文 SHA allowlist 发送 Ark embedding；真实客户数据、自由输入、PDF、图片、原名、对象键、路径、外部 LLM/OCR 均禁止。
- 最大风险：本地 OCR 资产与专属 RAG 运行时未证明可用；缺失时必须如实阻塞，不下载依赖。
- 验证预算：仍只运行 `tests.test_material_rag` 与 `localctl material-rag-verify`。原三轮及用户追加的唯一一次专属完整重放额度均已用完；不得自动追加下一次。
- 已实现：线性 `f1_0015`、三张 FORCE RLS 表、canonical unit 加密持久化、lease/retry 的 index/rebuild/delete job、每 scope 独立 RAGFlow dataset、逐 chunk 身份复核、上下文绑定的 QA 幂等/AAD、公开自由问题前置拒绝，以及无 host 端口的专属 Compose 栈。
- 真实材料门固定为 core 1/2/19/21：4 份、136 页、130 页原生文本、6 页本地 F0-H OCR；原件经两次 O_NOFOLLOW+SHA 稳定读取后，仅在仓库外 `/private/tmp` 生成 0600 opaque 快照，finally 删除。
- 目标检查红转绿：历史专属 runtime 曾执行 `python -B -m unittest tests.test_material_rag` 得到 `Ran 17 tests in 0.365s / OK`。本轮安全加固后测试集已增长到 27 项；见下方最新结果。
- 完整重放第 1 轮在容器启动前因 Demo 归档目录为 775/文件 664 被严格预检拒绝；改为仓库外 0600 SHA 快照，不修改原件。第 2 轮由缺少隔离 Keycloak issuer 的 3 个导入错误挡住；补固定不可路由 issuer 后目标检查转绿。
- 第 3 轮中 PostgreSQL、MinIO、Redis、ClamAV、MySQL、Elasticsearch、对象存储、Ark CONNECT 代理和本地 OCR 均 healthy；RAGFlow cache 因健康命令把密码变量单引号化而连续失败，RAGFlow、provider 配置、PDF 解析/索引/检索尚未开始。该一行已修，但按三轮上限未做第 4 次重放。
- 第 3 轮后先做未触网静态收口：公开 material QA 在 reservation 前拒绝；canonical unit 写入与 released source 行锁串行；lease/RLS 转移、dataset 唯一绑定、先落 provisioning intent 后建远端、`provisioning/deleting/deleted` 跨崩溃恢复、scope 级删除及迁移 downgrade 依赖顺序已加固；专属 localctl 控制态已完全移出共享 `.local`。这些修改现已由最新 27 项离线目标合同覆盖，但仍未通过完整专属栈运行门。
- 第 3 轮后的静态增量又加入 endpoint-aware Ark relay、正文 SHA 授权清单、独立断网 authorizer、普通 verifier 只读授权卷、RAGFlow provider 无探测文本的专属内部 bootstrap，以及验证器真值补强。新增授权已接入 2 个固定 canary、3 个固定查询和 6 个无原名／无业务 ID 的远端安全别名；provider、client A、client B 三域索引／检索及跨 scope 删除快照门已静态接线。三路最终静态审计未发现确定 P0/P1，但该结论不替代运行验证。
- 2026-08-13 使用当时获批的额外重放额度执行 `./scripts/localctl material-rag-verify`，固定输出 `LOCAL_MATERIAL_RAG_ARK_KEY_INVALID` 并以 exit 2 结束。命令先以 O_NOFOLLOW 只读 4 份 Demo 原件并完成稳定身份与完整 SHA 核验；随后只读元数据核对确认预期 key 文件当时不存在。失败发生在专属容器创建、页面正文解析、本地 OCR、RAGFlow/Ark 请求之前，因此该轮外发、OCR、索引和检索均为 0，不能形成 4 PDF 运行证据。
- 失败后专属 container/volume/network/带标签 runtime image 以及 control/private 临时目录均为 0；共享 `anhuan-f1` 未启停或复用。当前状态：`TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。
- 唯一完整重放失败后继续执行了不启动 Docker/服务/网络的直接目标单测。宿主 `python3` 共发现 27 项，最初结果为 3 failures、14 errors；14 errors 均在导入 `platform_foundation` 时因宿主缺少锁定依赖 `psycopg`，未安装依赖、未伪造模块。3 个真实静态漂移已修：relay 固定 authority 改为可直接审计的字面值、provider one-shot 同时清空大小写 `ALL_PROXY`、authorization writer 三处强制 `os.O_NOFOLLOW`；对应定向结果 `Ran 3 tests / OK`。
- 随后使用仓库外既有 `/private/tmp/f1lockvenv2`（Python 3.11.9、`include-system-site-packages=false`）核对 6 个关键锁定包版本全部一致，并设置不会联网的固定 `.invalid` Keycloak issuer，执行 `/private/tmp/f1lockvenv2/bin/python -B -m unittest tests.test_material_rag`，实际输出 `Ran 27 tests in 0.228s / OK`，errors=failures=skipped=0。该环境不是仓库受控产物，因此只记 `TARGETED_TEST_PASSED`，不替代专属镜像和完整栈 smoke。
- 工作树未暂存、未提交、未推送。完成门未满足，禁止记录 `LOCAL_MATERIAL_RAG_VERIFY_OK` 或本切片 `SMOKE_PASSED`；该条记录形成时，下一次运行仍需安全补齐专用 Ark key 和用户新的明确重放授权。
- 2026-08-13 用户要求直接查找并执行后，只读全机路径审计确认唯一历史副本为 `/private/tmp/anhuan-f111-formal-inputs-v1-rejected/provider/ark_api_key`。历史会话证明该包是在 F1.1.1 扩充 F0F 输入范围时整体改名后重建，`rejected` 不是 Ark 凭据判废；该副本生成于新 Ark key 已解除旧 401 并完成索引 E2E 之后。未读取、回显或记录 key 内容；仅将该单链接、当前用户所有的 0600 文件复制回权威 `/private/tmp/anhuan-f0j1-secrets/ark_api_key`，复核仍为 regular/0600/nlink=1/46 bytes。
- 恢复 key 后实际执行一次 `./scripts/localctl material-rag-verify`。命令越过 key 前置门，约 2 分钟后只输出 `LOCAL_COMMAND_FAILED`（exit 2）；finally 后专属 container/volume/network、带标签 image、control/private 目录均为 0，共享栈未启停。旧编排对 build、secret-init、stack start、migrator、authorizer、provider、RAGFlow restart 和 cleanup 的任一非零都丢弃捕获输出并折叠成同一 reason，因此失败阶段以及是否已经发生获准的 Ark embedding 请求均无法事后恢复，不能据此认定 OCR、索引或检索已运行或通过。
- 已最小修复上述诊断缺口：专属各阶段改为 `check=False`，只从严格 allowlist 提取固定 ASCII reason，否则返回固定阶段码；不输出 Docker stderr、正文、路径、ID 或凭据。唯一直接检查 `python3 -m py_compile scripts/localctl` exit 0。随后申请重放被安全审批器拒绝，命令未执行；审批器要求用户在获知会再次向 Ark 发送已登记脱敏文本后，另行给出一次字面明确的重放授权。当前仍为 `TARGETED_TEST_PASSED / MATERIAL_RAG_SMOKE_BLOCKED / NOT_PRODUCTION`。
- 最终授权重放先暴露 `LOCAL_MATERIAL_RAG_UNIT_TESTS_FAILED`；对应静态断言修复后定向检查通过。随后修复 authorizer 的 OCR UID/权限与 Compose 固定失败分类，相关定向检查通过；最后固定停在 `LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED`。这些局部证据不代表 4 份 Demo PDF 的解析、OCR、索引、检索、隔离与清理主链已通过。
