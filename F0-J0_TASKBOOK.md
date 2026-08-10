# F0-J0 任务书：成熟检索方案选型与可撤销探针（RAGFlow vs OpenSearch）

你是执行者，本书是唯一任务来源；中途没人可问，拿不准的写 `BLOCKED.md`，跳过继续别项。
断线先读 `PROGRESS.md` 最后一节接着做；每完成一项任务立即在 `PROGRESS.md` 追加 ≤12 行回执并贴真实命令输出。
目标：用同一批 F0-I canonical child chunks，对 RAGFlow 与 OpenSearch 做同源机制对照，产出选型证据；探针可完全拆除，不留下第二事实源。
冲突时：租户/数据安全与证据正确 > 可恢复 > 可复现 > 覆盖率 > 速度。
"只允许/不许"违反即失败；"建议"可换，但须在 `PROGRESS.md` 记原因。

## 我替领导拍的板

- 本轮只做选型探针：索引导入、BM25/关键词检索、机制核对表、资源实测、选型记录。**不做** embedding、向量召回、重排、LLM、问答、UI、F0-J1 正式接入。
- 检索质量与准确率**不在本轮范围**：只测机制（ID 往返、幂等、删除、重建、过滤、重启、租户），不宣称"检索效果好"。
- 探针数据授权依据 D06（2026-08-04 用户书面确认）：26 份 Fixture 允许本机/隔离环境开发与评估。明文 chunk 只允许出现在进程内存和探针容器的命名 Docker 卷内；**禁止写入宿主文件系统的任何明文导出文件**；探针结束必须删除全部容器与卷并验证为零。
- Docker 镜像拉取允许（与 F0-D 拉 PostgreSQL 同例），首次拉取后登记 digest；**运行期业务数据零外发**：所有服务端口只绑 `127.0.0.1`，不配置任何外部 LLM/embedding/API key。
- RAGFlow 版本固定 `v0.26.4`，OpenSearch 版本固定 `3.8.0`。本机为 Apple Silicon（arm64）：OpenSearch 官方镜像原生支持 arm64；**RAGFlow 官方不提供 arm64 镜像，必须按其官方文档自行构建**（darwin/arm64 可构建；需按官方说明调整 xgboost 版本并安装 unixODBC；Infinity 引擎在 arm64 不受官方支持，只准用其默认 Elasticsearch 路线）。
- **预授权降级分支**：先跑通 OpenSearch 路线（预期顺利），再试 RAGFlow。RAGFlow arm64 构建最多尝试 2 次；2 次均失败，把两次原始失败输出（脱敏）写入 `BLOCKED.md`，本轮按"OpenSearch 单路线 + RAGFlow arm64 硬条件失败证据"收口，这满足准入门规则，不算任务失败，不需要中途请示。
- RAGFlow 若在无外部 API key 时无法建库/检索（例如强制要求 embedding 模型且 arm64 构建无本地内置模型），记为硬条件失败（依赖外部服务），同上收口；容器内置且零外发的本地模型允许使用。
- 若某项检查两条路线都通过，选型倾向按路线文档既定原则：运维负担与收益比较，OpenSearch 是保底默认，RAGFlow 需要证明额外收益值得其 MySQL/MinIO/Redis/ES 附加运维。最终"选定"仍由领导拍板，本轮只交证据。

## 界限

只允许改/建：`src/platform_foundation/f0j0/**`、`tests/test_f0j0_retrieval_probe.py`、`infra/f0j0/**`、`artifacts/f0j0-retrieval-selection/v0.1/**`、`PROGRESS.md`（追加）、`BLOCKED.md`（追加）；探针运行物限 `/private/tmp/anhuan-f0j0-*`（0700）与 `anhuan-f0j0-*` 命名的 Docker 容器/卷/网络。其余一律只读。
**冻结（任一不符 → 原始输出置 BLOCKED.md 顶部并停止）**：
- F0-I 三产物 SHA 必须始终为 acceptance=`8a4c58cfed9dda5dd2514c44028a24e916d99431704deac8ddb8a07a9a897d1a`、status=`eb38e014589e2fcb5cfd7671ec5ccc4fe8f0eeb8996f2c5069e1e16b01f9cc5a`、sbom=`b7fa245e8fa97fce0b937ca97bfcf292469a3bac94a6d9e2ea4893caa4c4e8b5`。
- 两份源 manifest SHA=`e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae` / `2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04`。
- Alembic 唯一 head=`f0d_0006`；**禁止新增任何 migration**。
- PostgreSQL 全程只读：不 INSERT/UPDATE/DELETE/DDL，不创建新库；合成租户 B 数据只进探针索引，永不进 PostgreSQL。
不读 `.env.local`；不复制/修改/移动 26 份原件；不改 F0-A～F0-I 任何源码、测试、产物、锁文件；不引入 LangChain/LlamaIndex；不打开 acceptance.json 里的任何 closed gate。

## 泄漏红线（历史事故已多次复发，逐条执行）

1. 聊天与 PROGRESS/BLOCKED 里只允许出现：聚合计数、布尔、退出码、SHA-256、脱敏 reason code。**禁止出现**：DSN/口令、源文件名、chunk/block 正文、密钥字节、含用户名的绝对路径（`/Users/...` 一律写成 `<WS>`）。
2. 禁止对 `config.py`、`bootstrap.py`、任何测试文件的 DSN 段做整文件/大范围读取输出；需要接口就用 `grep -n "def " <file>` 只看函数名。
3. 校验原件一律在登记根目录用 `shasum -s -c`（静默）并只报退出码；禁止在错误工作目录重试。
4. 诊断命令禁止 `|| true`；失败就贴原始退出码。
5. OpenSearch/RAGFlow 的管理口令用 `openssl rand -hex 16` 生成，写入 `/private/tmp/anhuan-f0j0-secrets/`（0700/0600），聊天里永不出现。

## 任务0：基线与前置闸门

1. 逐项复核并在回执贴聚合结果：
   - `shasum -a 256` 实算 F0-I 三产物 = 上述冻结值；
   - `docker ps` 含 `anhuan-f0d-postgres-1` 且 healthy（不在则 `docker start anhuan-f0d-postgres-1`）；
   - `test -f /private/tmp/anhuan-f0i-acceptance-v01.key` 存在且 `stat` 为 0600/32 bytes（缺失 → BLOCKED，停止，等领导决定重放）；
   - F0-I 目标库唯一存在：用既有 `platform_foundation.f0i` 配置模块建立连接（禁止硬编码 DSN），确认 `alembic_version=f0d_0006`；
   - 带租户上下文只读聚合七表基线计数（configuration/run/document_scope/page/block/chunk/chunk_block_link）并记录为 `BASELINE`，同时记录唯一 child chunk ID 总数，预期=300（≠300 → BLOCKED）；
   - 全仓回归 `PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` 预期 `Ran 690 tests / OK`。
2. 记录本机快照：`uname -m`（预期 arm64）、Docker version、磁盘余量。是否存在 `.git` 只记录、不创建。
3. Docker 残留预检：`docker ps -a --format '{{.Names}}' | grep '^anhuan-f0j0-'` 与 `docker volume ls -q | grep '^anhuan-f0j0-'` 都必须为 0。

## 任务1：路线A —— OpenSearch 3.8.0 探针

1. 生成管理口令（见红线5）。启动：
   `docker run -d --name anhuan-f0j0-opensearch -p 127.0.0.1:9200:9200 -e discovery.type=single-node -e OPENSEARCH_JAVA_OPTS="-Xms1g -Xmx1g" -e OPENSEARCH_INITIAL_ADMIN_PASSWORD="$(cat <secrets>/os_admin)" -v anhuan-f0j0-osdata:/usr/share/opensearch/data opensearchproject/opensearch:3.8.0`
   健康判定：`curl -ks -u admin:*** https://127.0.0.1:9200` 返回 JSON 且 `version.number=3.8.0`（自签 TLS，探针允许 `-k`，记录该放宽）。登记镜像 digest（`docker inspect --format '{{index .RepoDigests 0}}'`）。确认端口只绑 loopback（`docker port` 输出含 `127.0.0.1`）。
2. 适配器 `src/platform_foundation/f0j0/`：
   - 只用标准库 `urllib.request`+`ssl`（探针专用，不进生产面）与既有 `psycopg`；复用 `platform_foundation.f0i` 的连接/租户上下文/解密重组路径读取 300 个 child chunk；若既有模块无公共只读入口，新增最小包装函数，**不复制密码学/DSN 代码**。
   - 每个 chunk 构造索引文档：`_id=chunk_id`，字段 `chunk_id, parent_chunk_id, document_id, tenant_id, kind, char_count, pages[]`（经 chunk_block_link→block→page 关联）+ `body`（明文，仅入索引）。**不含**源文件名（库中本就只有 opaque ID）。
   - 中文用默认 standard analyzer；中文分词插件（IK 等）记为后续评估项，不装。
   - 导出全程流式：DB→内存→bulk API，宿主磁盘零明文文件。
3. 机制核对表（每项在 `tests/test_f0j0_retrieval_probe.py` 落为可重跑断言，实测后在回执记 PASS/FAIL）：
   - C1 arm64 部署：容器健康且为 arm64 镜像。
   - C2 ID/metadata 保留：300/300 文档往返，逐文档字段 SHA-256 比对（不打印内容）。
   - C3 增量导入幂等：全量重导 → `_count` 仍=300、零重复。
   - C4 删除同步：删除某一 document_id 的全部 chunks → 计数=300−该文档数；重导恢复=300。
   - C5 清空重建：删索引→重建→计数与逐文档字段 hash 集合与 C2 一致。
   - C6 父子回链：任取 10 个命中 child，`parent_chunk_id` 在 PostgreSQL 中可解析且属同 document。
   - C7 metadata filter：按 document_id、按 pages 过滤，返回集恰为该范围。
   - C8 引用回传：3 组中文查询词（从 XLSX/DOCX 结构字段特征构造，避免整句正文入聊天）top-5 命中 → chunk_id 回 PostgreSQL 带租户上下文复核 → 全部可解密重组并定位 page；复核失败数=0。
   - C9 进程重启：`docker restart` → 计数不变、C8 同查询可复跑。
   - C10 零跨租户候选：构造 5 条合成租户 B chunk（合成 UUID、合成正文、内含与 A 共用的诱导词）只写入索引；以 A 租户上下文执行 C8 流程：原始候选集必须 ≥1 条 B（证明索引层会串）、PostgreSQL 复核后授权结果中 B=0（证明复核有效）；再验证 tenant_id filter 可排除 B 但**仍不作为授权依据**记录。
   - C11 资源实测：`docker stats --no-stream` 内存、`docker system df -v` 卷占用（只记数字）。
   - C12 零外发：无任何外部 API key 配置；除镜像拉取外无出网需求说明。
4. 回执含 12 项结果矩阵与索引文档总数（含合成 B 后=305）。

## 任务2：路线B —— RAGFlow v0.26.4 探针

1. 按 RAGFlow 官方"Build Docker image"文档在 darwin/arm64 构建（源码 checkout 固定 tag `v0.26.4`；按官方说明处理 xgboost/unixODBC；文档引擎用默认 Elasticsearch，**不用 Infinity**）。**最多 2 次构建尝试**；每次失败保存脱敏原始错误尾部 30 行到 `infra/f0j0/ragflow-build-attempt-N.log` 并在 BLOCKED 登记。2 次失败 → 走预授权降级分支，跳到任务3。
2. 构建成功则以官方 compose 启动，全部端口改绑 `127.0.0.1`，容器/卷统一 `anhuan-f0j0-` 前缀（compose project name `anhuan-f0j0-ragflow`）；登记全部镜像 digest。不配置任何外部 LLM/embedding provider；仅允许容器内置本地模型。若建 dataset 强制要求外部模型 → 硬条件失败，留证据，走降级分支。
3. 初始化若必须 Web UI 手工步骤：在回执列出精确步骤清单请领导本人执行一次（预计 <5 分钟），期间转入任务3可先行项；禁止自己造非官方 API 绕过。
4. 用与路线A**完全相同的 300 个 chunk**（同一适配器读取路径）经 RAGFlow HTTP API 导入：不上传原件、不让 RAGFlow 重新解析/OCR；`chunk_id/parent_chunk_id/document_id/tenant_id/pages` 必须以其 chunk metadata 能力存储并在检索结果中回传。
5. 执行与任务1相同的 C1～C12（C2～C5 以其 dataset/chunk API 等价操作实现；某项 API 不支持 → 该项 FAIL 并记机制原因，不算探针中断）。
6. 回执含 12 项矩阵 + 与路线A的逐项对照 + 附加运维面清点（实际起了几个容器、总内存、总卷占用）。

## 任务3：拆除与零残留验证

1. 全部探针容器 `docker rm -f`、卷 `docker volume rm`、网络清理；验证三个 grep 均为 0 输出：`docker ps -a | grep anhuan-f0j0-`、`docker volume ls | grep anhuan-f0j0-`、`docker network ls | grep anhuan-f0j0-`。
2. `/private/tmp/anhuan-f0j0-secrets` 删除；`find /private/tmp -maxdepth 1 -name 'anhuan-f0j0-*'` 输出=0。
3. PostgreSQL 零写入终审：重跑任务0的七表聚合，逐项 == `BASELINE`；run 计数仍=2。
4. F0-I 三产物 SHA 复算仍等于冻结值；26 份原件在登记根静默 `shasum -s -c` 退出码=0。
5. 镜像保留与否都可，但 digest 必须已登记在 selection 产物中。

## 任务4：选型记录与收口

1. 生成 `artifacts/f0j0-retrieval-selection/v0.1/selection.json` + `selection.md`（目录 0700、文件 0600）：
   - 双路线 12 项矩阵、资源实测、镜像 digest、失败证据引用（如走降级分支）；
   - 结论字段只允许 `RECOMMEND_OPENSEARCH` / `RECOMMEND_RAGFLOW` / `RAGFLOW_HARD_CONDITION_FAILED_OPENSEARCH_ONLY`，附 `final_decision_pending_leader=true`；
   - 固定声明：`SEARCH_NOT_READY`（探针已拆除）、`ACCURACY_NOT_EVALUATED`、`NOT_PRODUCTION`、`FIXTURE_ONLY`、检索质量未评价。
2. 连续生成两次，两文件 SHA 逐字一致（内容不含时间戳以外的可变项；日期固定写本轮开始日）。
3. 卫生扫描并贴聚合结果：两产物内 DSN/口令/源文件名/chunk 正文/`/Users/` 绝对路径命中均=0；与 26 源 hash 交集=0。
4. 全仓回归收口：`Ran 690 tests / OK` 不变（探针测试文件若依赖已拆除的容器，测试内部必须在容器缺失时以明确 skip reason 跳过——这是本项目**唯一允许** skip 的场景，且 skip 数必须在回执如实登记，不得计入"skipped=0"声明）。
5. PROGRESS.md 追加最终回执；BLOCKED.md 登记全部卡点与流程事故（含"无事故"也要写明）。

## 规矩

禁止 skip/todo（除任务4第4条唯一例外）、mock 被测引擎、删改旧测试、放宽阈值、改冻结件、吞异常、`|| true` 假绿。
同一验收连败 3 次换项；全书最多 8 轮；第 8 轮如实交付卡点与半成品清单。
每条回执贴实际命令输出（含红→绿）；失败输出先脱敏再贴。

## 完成条件

1. 12 项机制核对表：双路线各有实测结果，或 OpenSearch 全量结果 + RAGFlow 2 次构建失败/硬条件失败的原始证据。
2. PostgreSQL 七表与 run 计数 == BASELINE（零写入）；F0-I 三产物与 26 原件零漂移。
3. 探针容器/卷/网络/明文/secrets 残留全部=0，镜像 digest 已登记。
4. selection 两产物双跑 SHA 一致、卫生扫描全 0、结论字段合法且标记待领导拍板。
5. PROGRESS/BLOCKED 已更新；未打开任何闸门；未宣称检索质量、准确率或生产可用。
