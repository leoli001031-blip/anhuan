你是执行者，本书是唯一任务来源；中途没人可问，拿不准的写 `BLOCKED.md`，跳过继续别项。
断线先读 `PROGRESS.md` 接着做；每完成一项立即更新。
目标：把 F0-C 证据接入可重放的本地上传底座，并把未确认 P0 变成默认关闭、不可绕过、可审计的系统闸门。
冲突时：租户/数据安全与证据正确 > 可恢复 > 可复现 > 覆盖率 > 速度。
“只允许/不许”违反即失败；“建议”可换，但须在 `PROGRESS.md` 记原因。

## 我替领导拍的板
- 本轮只做 F0-D 后端纵向切片：PostgreSQL RLS、本地 Fixture vault、上传、幂等、audit/outbox/job/单次 Worker、F0-C 挂接和 readiness；不做 UI/OIDC/S3/AV/OCR/搜索/Gold 标注/法规/finding/报告。
- 使用真实 PostgreSQL 18，绝不用 SQLite 冒充 RLS；镜像首次拉取后锁 digest。项目 `.venv` 固定依赖，不升级全局环境。
- vault 仅为本轮 `/private/tmp` 隔离副本，目录/文件 0700/0600、opaque key、原件只读；副本不提交、不外发，状态只叫 `FIXTURE_STORED`。
- 五闸门固定：`pilot_context=UNCONFIRMED`、`region_industry=UNCONFIRMED`、`benchmark_tier=NONE`、`external_processing=DENY`、`professional_authority=UNASSIGNED`；无开闸 API，Fixture 仍可入库，UAT/生产/外部调用/专业发布必须失败。
- 只读 readiness 可展示关闭原因；真实客户、地区行业、Acceptance Gold、供应商授权和专业责任人仍等书面确认。

## 界限
只允许改：`F0-D_TASKBOOK.md`、`requirements/f0d.lock`、`alembic.ini`、`infra/f0d/**`、`migrations/**`、`src/platform_foundation/**`、`tests/test_platform_foundation.py`、`tests/f0d_reverse_verify.py`、`artifacts/f0d-upload-foundation/v0.1/{acceptance.json,status.html,sbom.json}`、`PROGRESS.md`、`BLOCKED.md`；`.venv/**` 和本轮 Docker 容器/卷仅作本机运行物。其余只读。
冻结：旧 125 tests；F0-A=`3096e49…3db99`、F0-B=`28646fe…ca075`、F0-C=`15ca3e7…358c9`；core/negative manifest=`e9425d…6316ae`/`2238a2…20e04`；F0-C full-plan=`08c8a3…34436`。不读 `.env.local`，不改/删/复制到工作区的 Demo 原件，不联网调用业务 provider。

## 现状与任务0
2026-08-05 实测：Python 3.11.9；Docker CLI/daemon 29.6.2；FastAPI 0.133.1、SQLAlchemy 2.0.51、Pydantic 2.13.4、Uvicorn 0.41.0；psycopg/Alembic/本地 psql 缺失；磁盘余 863GiB。
先复跑旧 tests、冻结指纹、26 源 hash、Docker/Compose；建隔离 venv、拉并锁 PostgreSQL 镜像。任一冻结项不符，把原始输出置于 `BLOCKED.md` 顶部，停止真实 Fixture，只做合成样本；通过后追加 ≤10 行开工回执。

## 任务1：数据库与关闭态治理
建 migration owner/runtime/worker 分权；租户表 `enterprise_id NOT NULL`、复合 FK、`ENABLE/FORCE RLS`、`USING+WITH CHECK`，runtime 无 owner/superuser/BYPASSRLS/DDL/TRUNCATE。只建最小上传、object/document/version、idempotency、audit/outbox/job、processing plan/unit 与只读 capability gate。A/B canary 实测无上下文默认拒绝、跨租户泄漏0、连接复用无残留；五 P0 缺失/篡改/过期一律关闭。

## 任务2：上传与可恢复处理
API 不接受磁盘路径或客户端 tenant；本地 session 映射 actor/enterprise。登记 source ID 后，受限 fd 流式复制、hash/fstat/读后 stat、O_EXCL+fsync 晋升并复读校验；complete 同事务写 version+audit+outbox。relay 幂等建 job，Worker 用 SKIP LOCKED、lease generation/token 和 CAS；job 只存 ID。F0-C 仅在身份/hash匹配时挂接 26 entries/249 visual units/225 native/24 OCR候选/2 DOC deferred，绝不执行 OCR。

## 任务3：防退化与真实重放
新增 ≥32 项独立 unittest，总数 ≥157、skipped=0；不得 mock DB/RLS/vault/被测服务。覆盖 RLS、复合 FK、幂等冲突、并发 complete、不可覆盖、symlink/hardlink/FIFO、短写/崩溃、重复 relay、旧 lease、P0 绕过、正文/路径泄漏和故障健康。反向脚本打印 0→2→0 及 tenant/body/external/OCR/gate bypass 全0。smoke→full→full；两次 full 业务行/对象不增，26 blobs/versions、41878200 bytes，源 hash 不变。

## 规矩
禁止 skip/todo、删改旧测试、放宽阈值、mock 核心对象、改冻结件、吞异常、`|| true` 假绿。审计写失败时高风险命令失败关闭。结果变差即回滚自己的改动并记录；同一验收连败3次换项；最多8轮。

## 完成条件
1. 真 PostgreSQL 上 A/B 隔离、上传不可变、幂等/outbox/job恢复、F0-C 挂接和五 CLOSED gate 全部可重放；外部/OCR/Gold晋级/专业结论调用均0。
2. 总 tests≥157、skipped=0；反向验证全绿；四冻结指纹、26源 hash 不变，产物无正文/文件名/路径/电话邮箱/远程资源。
每条贴实际命令输出（含红→绿）；`BLOCKED.md` 随交付提交，或8轮后如实交付卡点。
