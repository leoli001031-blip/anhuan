# F0-J1 任务书：RAGFlow + 豆包 embedding + 证据化问答

你是执行者，本书是唯一任务来源；中途没人可问，拿不准的写 `BLOCKED.md`，跳过继续别项。
断线先读 `PROGRESS.md` 最后一节接着做；每完成一项任务立即在 `PROGRESS.md` 追加 ≤12 行回执并贴真实命令输出。
目标：在 RAGFlow + 豆包 embedding 栈上，交付**检索 + 证据化问答**：查询 → RAGFlow 召回 → PostgreSQL RLS 复核 → LLM 生成带 citation 的回答；无证据则拒答。
冲突时：租户/数据安全与证据正确 > 可恢复 > 可复现 > 覆盖率 > 速度。
"只允许/不许"违反即失败；"建议"可换，但须在 `PROGRESS.md` 记原因。

## 我替领导拍的板

- **前置决定已固化**：F0-J0 选定 RAGFlow v0.26.4 + 火山引擎 Ark `doubao-embedding-vision` 作为检索引擎；接受 4 组件运维与数据外发 Ark 为常态。已登记在 `artifacts/f0j0-retrieval-selection/v0.1/selection.json` 中。
- **RAGFlow 只定位为 `Retrieval Sidecar`**，不是业务事实源；候选 chunk ID 必须回 PostgreSQL 做企业、版本、密级、状态鉴权后才能进入 LLM 上下文。
- **本轮交付检索 + 证据化问答**：业务语料域查询 → RAGFlow 召回 → PostgreSQL RLS 复核 → 授权正文 + 页码/bbox 引用 → LLM 生成带 citation 的回答。**不做**聊天产品、不做 UI、不对外发布、不替代环保专业判断。
- **检索质量与准确率不在本轮范围**：只验证链路完整；不宣称"检索效果好"或"回答准确"。
- **数据授权依据 D06**：26 份 Fixture 允许本机/隔离环境开发与评估。明文 chunk 只允许出现在进程内存、RAGFlow 容器命名卷和 PostgreSQL pgcrypto 密文；**禁止写入宿主文件系统的任何明文导出文件**。
- **Embedding 与 LLM 分离**：
  - Embedding 由 RAGFlow 通过火山引擎 Ark `doubao-embedding-vision` 完成；Ark API key 写入 `/private/tmp/anhuan-f0j1-secrets/ark_api_key`（0600）。
  - LLM 由 DeepSeek 官方 API 完成；DeepSeek API key 写入 `/private/tmp/anhuan-f0j1-secrets/deepseek_api_key`（0600）。
  - LLM 模型默认 `deepseek-v4-pro`；由 deepseek-v4-flash 升级而来，因引用格式遵循率显著更高。
- Docker 镜像拉取允许，端口只绑 `127.0.0.1`，运行期不出网到 Ark/DeepSeek 以外。

## 边界

只允许改/建：`src/platform_foundation/f0j1/**`、`tests/test_f0j1_retrieval_qa.py`、`infra/f0j1/**`、`artifacts/f0j1-retrieval-qa/v0.1/**`、`PROGRESS.md`（追加）、`BLOCKED.md`（追加）。运行物限 `/private/tmp/anhuan-f0j1-*`（0700）与 `anhuan-f0j1-*` 容器/卷/网络。其余只读。

**冻结（任一不符 → 原始输出置 BLOCKED.md 顶部并停止）**：
- F0-I 三产物 SHA=`8a4c58cf…/eb38e014…/b7fa245e…`；F0-J0 两产物 SHA 开工前复核登记。
- 源 manifest SHA=`e9425d…/2238a2…`。
- Alembic head=`f0d_0006`；**禁止新增 migration**。
- PostgreSQL 只读；合成租户 B 数据只进 RAGFlow 索引，永不进 PostgreSQL。

不读 `.env.local`；不改 F0-A～F0-J0 源码/测试/产物/锁文件；不引入 LangChain/LlamaIndex；不打开 acceptance.json 里的 closed gate。

## 泄漏红线

1. 聊天/PROGRESS/BLOCKED 只出现：聚合计数、布尔、退出码、SHA-256、脱敏 reason code。**禁止**：DSN/口令、源文件名、chunk 正文、密钥字节、绝对路径、LLM 完整回答原文。
2. 禁止整文件输出 config/bootstrap/测试 DSN 段。
3. 原件校验用 `shasum -s -c` 静默，只报退出码。
4. 禁止 `|| true`。
5. Ark key 与 DeepSeek key 仅存 secrets 目录（0600），聊天永不出现。
6. LLM 测试只断言 citation 存在/拒答触发/长度范围，不写回答原文。

## 任务0：基线与前置闸门

1. 实算 F0-I 三产物 = 冻结值；F0-J0 两产物 SHA 复核登记。
2. `docker ps` 确认 `anhuan-f0j0-`/`anhuan-f0j1-` 残留为 0；`anhuan-f0d-postgres-1` healthy。
3. `/private/tmp/anhuan-f0i-acceptance-v01.key` 存在且 0600/32 bytes（缺失 → BLOCKED）。
4. F0-I 库 `alembic_version=f0d_0006`；七表聚合 == BASELINE（configuration=1/run=2/document_scope=26/page=249/block=1909/chunk=553/link=1636，child=300）。
5. 全仓回归 `Ran 690 tests / OK`。
6. Ark key 存在：`test -s /private/tmp/anhuan-f0j1-secrets/ark_api_key` 且 0600（缺失 → 停）。
7. DeepSeek key 存在：`test -s /private/tmp/anhuan-f0j1-secrets/deepseek_api_key` 且 0600（缺失 → 停）。

## 任务1：RAGFlow + 豆包 embedding 运行栈启动

1. 复用 F0-J0 arm64 镜像；不存在则按官方流程重建一次（最多 1 次，失败 → BLOCKED）。
2. 创建 `infra/f0j1/.env` + `docker-compose.yml`：全部端口绑 `127.0.0.1`；project=`anhuan-f0j1-ragflow`；ES/MySQL/MinIO/Redis 容器/卷统一 `anhuan-f0j1-` 前缀；配置 VolcEngine provider + `doubao-embedding-vision@VolcEngine`，key 从 secrets 注入。
3. `docker compose up -d`；健康判定：RAGFlow API 200/JSON、四依赖 healthy；登记全部镜像 digest。
4. 若需 Web UI 初始化，列出精确步骤请领导执行；禁止造非官方 API 绕过。

## 任务2：适配器与 300 child chunk 导入

1. `src/platform_foundation/f0j1/` 创建：
   - `reader.py`：复用 F0-I/F0-F 解密路径，不复制密码学/DSN；流式读 300 child chunk。
   - `ragflow_client.py`：封装 dataset/chunk/retrieval HTTP API。
   - `index_schema.py`：metadata 字段 `chunk_id, parent_chunk_id, document_id, tenant_id, kind, char_count, pages[]` + `body`。
   - `retrieval.py`：`search(query, domain)` → 候选 chunk ID。
   - `citation.py`：RLS 复核 + 解密重组 + 页码/bbox 引用。
   - `llm_client.py`：DeepSeek 官方 API 封装，默认模型 `deepseek-v4-pro`（由 v4-flash 升级，引用格式更稳）。
   - `qa_service.py`：检索→复核→citation→LLM；prompt 强制每句事实引用 chunk_id，无证据拒答。
2. 向 RAGFlow 创建 dataset，导入 300 chunk：不上传原件、不重新 OCR；metadata 必须可回传。流式 DB→内存→API，宿主零明文文件。
3. RAGFlow chunk 计数=300；逐文档字段 hash 集合与 DB 侧一致。

## 任务3：机制核对表 C1~C12

全部在 `tests/test_f0j1_retrieval_qa.py` 落断言；回执记 PASS/FAIL：

- C1 arm64 部署：5 容器 healthy，镜像 arm64。
- C2 ID/元数据 300 往返：按 `chunk_id` 召回，字段 SHA 比对。
- C3 增量导入幂等：重导计数不变。
- C4 删除同步：删某 document chunks 后计数正确，重导恢复。
- C5 清空重建：删 dataset 重建后 hash 集合一致。
- C6 父子回链：10 个命中 child 的 parent 在 PG 可解析且同 document。
- C7 metadata filter：按 document_id/pages 过滤返回集正确。
- C8 引用回传：3 组中文查询 top-5 → chunk_id 回 PG 复核 → 全部可解密重组定位 page。
- C9 进程重启：`docker compose restart` 后 C8 可复跑。
- C10 零跨租户候选：合成租户 B 只进索引；A 上下文原始候选含 B，PG 复核后 B=0。
- C11 资源实测：`docker stats`/`docker system df` 只记数字。
- C12 外发审计：记录 Ark embedding + DeepSeek LLM 调用次数与字节数（聚合计数）；除 Ark/DeepSeek/镜像拉取外无其他出网。

## 任务4：证据化问答服务

1. **只接受业务语料域**：不接受客户端索引名/dataset 名/表名/库名，拒绝此类参数。
2. **链路**：RAGFlow 召回 → PostgreSQL RLS 复核 → 未通过丢弃并审计 → 通过则解密重组正文 + 页码/bbox → LLM prompt。
3. **Prompt 强制规则**：
   - 每句事实必须附带 `chunk_id` 引用；
   - 无法引用则明确说"根据已有资料无法确认"；
   - 不得编造未提供资料中的事实；
   - 回答末尾列出全部引用 chunk_id + page。
4. **拒答规则**：命中为 0、全部候选未通过鉴权、正文不可重组、LLM 拒绝引用时，返回明确拒答 reason code。
5. **返回内容安全**：不含源文件名、密钥、未授权正文；返回字段限定为 `answer, citations[{chunk_id, document_id, pages[], bbox[], snippet}]`。
6. **测试断言**：合法查询返回带 citation 回答；越权查询拒答；非法索引参数拒绝；无证据查询拒答；合成租户 B 诱导查询复核后回答中无 B 的 snippet。

## 任务5：拆除与零残留验证

1. 默认保留栈用于开发/测试，但必须具备可拆除能力。
2. 至少执行一次拆除：`docker compose down -v`；三 grep（ps/volume/network 的 `^anhuan-f0j1-`）退出码均=1；`anhuan-f0j1-secrets` 删除；`find /private/tmp -maxdepth 1 -name 'anhuan-f0j1-*'` 输出=0。
3. 拆除后重建并复跑 C1/C3/C8 验证可重建；失败 → BLOCKED。
4. PostgreSQL 七表 == BASELINE；F0-I 三产物 SHA 复算仍冻结；26 源 `shasum -s -c` exit=0。

## 任务6：产物与收口

1. 生成 `artifacts/f0j1-retrieval-qa/v0.1/retrieval_qa.json` + `retrieval_qa.md`（0700/0600）：
   - 12 项机制矩阵、资源实测、镜像 digest、Ark 调用聚合；
   - 结论字段：`EVIDENCE_QA_READY_FIXTURE_ONLY`；固定声明：`ACCURACY_NOT_EVALUATED / NOT_PRODUCTION / FIXTURE_ONLY / CHAT_UI_NOT_BUILT / PROFESSIONAL_JUDGMENT_REQUIRED`；
   - 明确 RAGFlow=Retrieval Sidecar，PostgreSQL RLS=最终授权边界。
2. 连续生成两次，两文件 SHA 一致。
3. 卫生扫描：DSN/口令/源文件名/chunk 正文/`/Users/`/LLM 回答原文命中均=0；与 26 源 hash 交集=0。
4. 全仓回归：`Ran 690+N tests / OK`；栈缺失时的 skip 必须明确 reason 并如实登记 skip 数，不得计入"skipped=0"。
5. PROGRESS/BLOCKED 更新；未开任何闸门；未宣称准确率/生产可用。

## 规矩

禁止 skip/todo（除栈缺失明确 skip）、mock 被测引擎、删改旧测试、放宽阈值、改冻结件、吞异常、`|| true` 假绿。
同一验收连败 3 次换项；全书最多 8 轮；第 8 轮如实交付卡点与半成品。
每条回执贴实际命令输出（含红→绿）；失败输出先脱敏再贴。

## 完成条件

1. RAGFlow + 豆包 embedding 栈在 arm64 本机 healthy 启动，端口 loopback。
2. 300 child chunk 导入 RAGFlow，metadata 可回传。
3. C1~C12 全部实测登记。
4. 证据化问答：业务域查询 → 召回 → PG RLS 复核 → 授权正文 + 引用 → LLM 生成带 citation 回答；无证据/越权/非法参数均拒答。
5. PostgreSQL 七表 == BASELINE；F0-I 三产物与 26 原件零漂移。
6. 栈可拆除/重建；拆除验证三 grep=0；secrets 清理后 find=0。
7. 产物双跑 SHA 一致、卫生扫描全 0、结论字段合法。
8. PROGRESS/BLOCKED 已更新；未宣称准确率/问答准确/生产可用。
