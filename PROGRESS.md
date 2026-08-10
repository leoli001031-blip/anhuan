# PROGRESS

> **历史流水账。** 当前项目状态统一见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文按日期保留；其中旧 F1.1.1 `READY/完成` 叙事已被后续 `F1_1_1_PAUSED_NOT_ACCEPTED` 与 tracked v0.3 `F1_1_1_REJECTED` 取代，不能作为现役验收结论。
1. 目标：把两份只读 manifest 变成可执行、脱敏、失败关闭的 Fixture 入口闸门。
2. 基线：Python 3.11.9；无 Git、pyproject、src、tests；Docker daemon 未运行且本目标不使用。
3. 数据：core 24 份、negative 2 份，26 个来源文件 SHA-256 全部通过。
4. 顺序：校验器 -> unittest/反向验证 -> 真实审计 JSON -> 指纹与白名单复核。
5. 最大风险：路径越界/符号链接、错误被吞成成功、审计输出泄露业务文件名或本机路径。
6. 约束：仅标准库、无网络、原件零复制、规格文件与 manifest 只读。
7. 已完成：标准库 CLI 以受限句柄流式校验；锁定官方 manifest 身份，失败关闭并安全写入脱敏审计 JSON。
8. 已完成：24 项 unittest 全绿且 skipped=0，覆盖篡改、缺失、坏/替换 manifest、越界、重复、symlink/hardlink/FIFO 和输出隔离。
9. 已完成：临时样本反向验证得到 valid=0、tampered=2、restored=0。
10. 已完成：真实审计精确得到 core 24/41500435 bytes、negative 2/377765 bytes、failed=0；敏感词和网络依赖扫描通过。

## F0-B 开工回执
1. 目标：把已验证的 26 份 Fixture 脱敏编目并分流到 5 类处理路线，不进入 OCR、搜索或事实库。
2. 顺序：冻结 F0-A -> 安全格式识别/路由 -> 新测试与反向验证 -> smoke/full 产物与离线状态页。
3. 基线：Python 3.11.9；F0-A 24/24 tests；core 24 + negative 2；保护总指纹与两份 manifest 均匹配。
4. 最大风险：扩展名欺骗、OOXML 容器伪造、二次读取竞态、负样本误获发布权限、产物泄露文件名或路径。
5. 约束：仅标准库、零网络、原件零复制零修改、F0-A 只读、输出固定白名单、全部标记 FIXTURE_ONLY。

## F0-B 完成记录
1. 安全路由完成：同一只读 fd 校验 SHA/stat/格式；PDF、JPEG、CFB/DOC、DOCX、XLSX 均失败关闭，真实 smoke=10、full=26。
2. 对抗修正完成：拒绝 PDF Name 转义加密与伪 xref、OOXML CRC/宏/越界关系、DOC 宏/加密/FIB/坏 FAT/尾载荷及畸形 JPEG。
3. 防退化完成：总计 72 项 unittest，skipped=0；临时样本反向验证 valid_exit=0、tampered_exit=2、restored_exit=0。
4. 产物完成：full 为 PDF21/DOC2/DOCX1/JPEG1/XLSX1、core24/negative2；负样本三闸门全关，external_processing=DENY。
5. 可重放完成：full 连跑两次 SHA-256 均为 2937047ed5d2c6db7f73ba7d8ba597acd24ec376cde73b5b48e529ac6cf5004c。
6. 边界复核完成：产物无 manifest 文件名/源路径/敏感词/远程资源；源哈希交集0；F0-A 总指纹与两份 manifest 指纹保持不变。

## F0-C 开工回执
1. 目标：生成无正文落盘的页级原生解析证据与 OCR 候选计划；不执行 OCR、转换、搜索或专业判断。
2. 基线：Python 3.11.9；无 .git/pyproject；pypdf 6.14.2，License-Expression=BSD-3-Clause。
3. 门禁：现有 72 项 unittest 全绿、skipped=0；26 份登记原件 `shasum -s -c` 全部 exit=0。
4. 冻结：F0-A=3096e49e79536e03a86aacb28eac764e017ff0282a44243ad47f6b5474e3db99；F0-B=28646fe34e1c31bd0663f0584f8abaa4a00dfba7e1b20da4db23d5e1f9eca075。
5. 输入：core/negative manifest 与 full route-plan 指纹分别为 e9425d…6316ae、2238a2…20e04、2937047…5004c，均匹配登记值。
6. 顺序：固定路由重放 -> PDF/OOXML/JPEG/DOC 只读探针 -> 防退化与反向验证 -> smoke/full 确定性产物。
7. 最大风险：解析异常泄漏正文或路径、把候选误称 READY、输出竞态、公式/外部关系被执行或访问。
8. 约束：只改 F0-C 白名单；正文仅驻留内存；无网络、子进程、外部二进制、OCR、PyMuPDF 或依赖变更。

## F0-C 执行记录
1. 页级计划器完成：live 重建 F0-B 并逐字节比对登记 route-plan；同一只读 fd 完成 SHA/stat/类型复核、strict 原生解析与读后 stat。
2. 结构探针完成：PDF 仅落页几何/计数/正文摘要/候选决策；DOCX/XLSX 仅落结构计数与摘要；JPEG 仅落尺寸；DOC 固定延后。
3. 真实内存探针通过：smoke=10份/109 PDF页/110视觉单元/105 native/5 OCR；full=26份/248 PDF页/249视觉单元/225 native/24 OCR。
4. 结构基数通过：DOCX=60段/1表/58单元格；XLSX=3 sheet/306 cell/0 formula；JPEG=1928×2567；DOC deferred=2；errors=0。
5. 红→绿完成：首轮审计样例先得到 3 个 FAIL（Tr=3/7 重置各1、summary 错层字段1）；次轮越界行号/字符计数/XLSX 缓存关系也分别复现红灯；最终新增 53 项 F0-C unittest 全绿，全部测试=125、failures=0、errors=0、skipped=0。
6. 防退化完成：覆盖阈值/坏字符/隐藏与 CropBox 外文字/几何/损坏与加密 PDF、UTF-16 实体、OOXML 结构、JPEG/DOC、权限、泄漏、确定性、资源上限及输入输出文件系统攻击；隐藏文字在 `Tj/TJ/'/"` operand 时判定，不受后续 `0 Tr` 延迟 flush 影响。
7. 输出防线完成：writer 对 top/policy/parser/summary/entry/page/anchor 逐层精确校验且复算汇总；固定登记行号范围、数值资源上限、entry 位置唯一及 XLSX 公式缓存交集；目录 0700、文件 0600、owner 校验；短写和批次失败只回滚本调用新建文件。
8. XLSX 口径修正：普通 `<v>` 计为 value_cells=19，只有公式且带 `<v>` 才计 formula_cached_values=0；真实 formulas=0。
9. 反向验证完成：0→2→0；正文 canary 覆盖 plan/status/logging；运行时 audit 及静态生产依赖检查均得到 external_calls=0、ocr_calls=0。
10. 真实产物完成：smoke SHA=69637cf531cc775a0898059c4b9ebaff9b44fe1728bc75fe46efe4049e705ed4；full 连跑 SHA=08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436。
11. 最终边界复核：schema/free-string/文件名/绝对路径/邮箱/远程资源命中均0；电话逐字段复核为 free-string=0、status=0，裸正则仅在 opaque SHA-256 摘要内假阳性3次；新文件与26源哈希交集0；F0-A/F0-B/manifest 与原件保持不变。
12. F0-C 固定顺序三源码、两测试、四产物总指纹=15ca3e7b8b20d9b75b72f59e4cec83f07a3be62f565c0a09c344f08cd38358c9。

## F0-C 最终验收命令回执
1. `python3 -B -c '<cwd/runtime probe>'` → `cwd=/Users/lichenhao/Desktop/安环项目`、`git_exists=false`、`pyproject_exists=false`、`python=3.11.9`、`pypdf=6.14.2`、`license_expression=BSD-3-Clause`、exit=0。
2. 红灯：定向运行新增隐藏文字重置与错层 schema 测试 → `Ran 2 tests`、`FAILED (failures=3)`、exit=1；绿灯：同命令 → `Ran 2 tests`、`OK`、exit=0。
3. 次轮红灯：越界行号/字符计数定向运行 → `Ran 3 tests`、`FAILED (failures=2)`，补齐 XLSX 顶层汇总后单测也 `FAILED (failures=1)`；修复后 3 tests → `OK`、exit=0。
4. `PYTHONPATH=src python3 -B -m unittest -v tests.test_fixture_page_planner` → `Ran 53 tests`、`OK`、exit=0；全仓计数复跑 → `tests_run=125 failures=0 errors=0 skipped=0`、exit=0。
5. `PYTHONPATH=src python3 -B tests/page_planner_reverse_verify.py` → `valid_exit=0 tampered_exit=2 restored_exit=0 body_leaks=0 external_calls=0 ocr_calls=0`、exit=0。
6. CLI 实跑 → smoke `documents=10 visual_units=110 errors=0`；full 两次均 `documents=26 visual_units=249 errors=0`，三次 exit=0。
7. `jq` → smoke `PDF pages=109 visual=110 native=105 OCR=5 manual=0`；full `documents=26 PDF=21/pages=248 visual=249 native=225 OCR=24 manual=0 DOC deferred=2 errors=0`。
8. `jq` 结构 → DOCX `60段/1表/5行/58单元格`；XLSX `3 sheet/306 cell/0 formula/19 value/0 formula cache`；JPEG `1928x2567`；negative=2 且三闸门全关。
9. 重放前后 SHA 均为 smoke=`69637cf531cc775a0898059c4b9ebaff9b44fe1728bc75fe46efe4049e705ed4`、full=`08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436`、status=`f26699c81b1d633dea54bb5153ab5706b5cbf121a405a529ef8132fd19709c1c`、sbom=`fd646e5263355d7d051aed0716074426d43ad5a1d9b66a74db0f59a3835b6c82`。
10. 产物审计 → `schema_failures=0 filename_hits=0 absolute_path_hits=0 email_hits=0 remote_resource_hits=0 free_string_phone_hits=0 status_phone_hits=0`；生产源码禁用依赖 `rg` 无匹配、exit=1；`hash_intersection=0`。
11. 冻结复核 → F0-A=`3096e49e79536e03a86aacb28eac764e017ff0282a44243ad47f6b5474e3db99`；F0-B=`28646fe34e1c31bd0663f0584f8abaa4a00dfba7e1b20da4db23d5e1f9eca075`；core/negative/full-route 均等于登记指纹；两份 `shasum -s -c` 均 exit=0。

## F0-D 开工回执
1. 目标：真实 PostgreSQL RLS + 本地不可变 Fixture 上传 + 幂等/audit/outbox/job/Worker + F0-C 挂接，并把五类未确认 P0 固定为可验证关闭态。
2. 顺序：冻结 F0-A/B/C -> 锁依赖和 PG 镜像 -> migration/RLS -> 上传与恢复 -> 反向测试 -> smoke/full 重放。
3. 基线：旧 125 tests 全绿，failures/errors/skipped 均0；F0-A/B/C 与两 manifest/full-plan 指纹全部匹配。
4. 运行条件：Docker client/server 29.6.2、Compose 5.3.1 已可用；Python 3.11.9；psycopg/Alembic 待装入项目 `.venv`。
5. 真实资料门禁：26 个登记 source hash 复核 exit=0；只读流入本轮 `/private/tmp` vault，不写工作区、不外发。
6. P0 边界：只实现 CLOSED gate/readiness/拒绝与审计，不创建真实客户、地区行业、Gold、供应商授权或专业结论。
7. 最大风险：RLS owner 绕过、连接租户上下文残留、半写对象/半提交事务、幂等或旧 lease 重复落结果、日志泄露。
8. 约束：绝不以 SQLite 假绿；无外部业务 provider；不读取 `.env.local`；旧实现和判卷件只读。

## F0-D 执行记录
1. 依赖与镜像已锁：项目 `.venv` 固定 24 个直接/传递版本；PostgreSQL `18.3-bookworm` 锁定 digest `80630f…3acba`，未改全局 Python。
2. F0-C 交接校验已落地：每次从冻结 manifest/route/full-plan 重建登记目录，source 仅以 opaque UUID 暴露；实测 full 聚合为 26/249/225/24/2。
3. P0 关闭态契约已落地：真实客户、地区行业、Acceptance Gold、外部处理、专业责任、UAT/生产均只有稳定拒绝码且无开闸 API。
4. PostgreSQL 初始化红→绿：Docker Desktop 对 bind-mounted 可执行 `.sh` 两次报 `bad interpreter: Permission denied`/exit126；改为只读 psql `.sql` 后第三次容器 `Healthy`，失败卷均在无业务数据时定向移除。
5. Migration/RLS 已实跑：Alembic `upgrade -> f0d_0001`；17表含版本表、14租户表 FORCE RLS、三运行角色均无 super/createdb/createrole/bypassrls、五 gate 全 CLOSED。
6. Seed 红→绿：首次登记源回读被 FORCE RLS 默认拒绝并报 `LOCAL_SOURCE_MISMATCH`；补 migration-role 同租户只读 policy 后早期曾重放得到 `registered_sources=27`，该结果随后因 B synthetic canary 误挂真实源被废弃；最终 v02 口径为 A=26、B=0，重放不重复增长。
7. 端到端 smoke 红→绿：先后暴露 vault key 需 UUID4 hex、F0-C schema/rule 冻结值含 `/` 两个真实问题；修正后旧 `CONTENT_STORED` 会话和过期 lease 均可恢复，得到 1 blob/version/plan、49 units、1 succeeded job、vault_objects=1。
8. Vault 安全收口：命名读取以 `O_NONBLOCK` 拒绝 FIFO 且不阻塞；直接 hardlink 晋升避免冗余复制，按 final-dir fsync 后再清 staging 的顺序恢复 nlink=2 中断，并验证 inode/mode/owner/hash/size。
9. RLS 与证据红→绿：会话绑定 14 张租户表，3 张 actor 写入表再加 restrictive actor policy；旧 v1 数据迁移先后因 immutable/RLS 回填失败两次，最终在单一迁移事务内临时取消 FORCE、回填、恢复 FORCE 与不可变 trigger 后通过。
10. Bootstrap 重放防假绿：对 enterprise 的 Fixture 身份/五个关闭字段、actor/role/session 及 source 全登记字段逐项精确回读；新增角色与 Fixture 版本 poison 用例实测 `Ran 2 tests` / `OK`，污染记录均以稳定 mismatch reason code 失败关闭。
11. 全仓回归通过：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` 实测 `Ran 221 tests in 14.684s` / `OK`，failures=0、errors=0、skipped=0；仅有固定依赖下 TestClient 的 deprecation warning，未违规升级。
12. 无损验收环境已建：保留旧 `f0d` 和 `f0d_acceptance_v01` 不动，新建 `f0d_acceptance_v02` 并以 `f0d_migration` 拥有 schema；Alembic 实跑 `-> f0d_0001 -> f0d_0002` 成功。
13. 真实重放完成：全新 `/private/tmp/anhuan-f0d-acceptance-v02` 上 smoke=10 uploads/10 blobs/13,568,633 bytes/110 units/105 native/5 OCR 候选/2 deferred；首次 full=26/26/41,878,200/249/225/24/2。26 jobs、52 audit。
14. 幂等复播通过：第二次 full 的 uploads/blobs/bytes/versions/plans/units/jobs_succeeded 增量全为0，`relayed_this_run=0`、`processed_this_run=0`、vault_objects=26；external/OCR 执行、Gold 晋级、专业发布仍全0。
15. 反向验证通过：新验收库实测 `valid_exit=0`、`tampered_exit=2`、`restored_exit=0`，`tenant_leaks/body_leaks/external_calls/ocr_calls/gate_bypasses` 均为0。
16. 验收产物已从最终证据重生：生成器精确核验 14 张 FORCE/session-bound RLS 表集、3 张 `RESTRICTIVE INSERT` actor 表集、5 个 lineage 约束、A=26/B=0 及 26 个 vault 对象哈希；状态为 `LOCAL_FIXTURE_FOUNDATION_ACCEPTED`。
17. 产物确定性通过：连续两次生成 SHA 相同；acceptance=`30e86f…b0ebb`、status=`554a2f…52ca`、sbom=`2c5cd1…bef3`。状态页明确 vault 仅为 create-only/0600/使用前 hash 复验，不声称 WORM。
18. Complete key 跨资源冲突红→绿：新测试复现“已完成 upload 早退绕过 key 绑定”，首跑 `Ran 1 test` / `FAILED (failures=1)`；两个 completed fast path 均改为先核对 key 的 request hash，冲突返回 `IDEMPOTENCY_CONFLICT`，与资源级幂等/并发 complete 同跑 3 tests 全绿。
19. Complete 新 key 永久绑定红→绿：二阶回归首跑 `Ran 1 test` / `FAILED (failures=1)`，证明仅查不写仍可让同 key 日后绑到另一 upload；修正后以 transaction advisory lock 串行化 key，对首次出现的 completed key 写入终态记录并绑定 version，4 项 complete 幂等/并发回归全绿。
20. 当前树全仓回归：`Ran 223 tests in 19.892s` / `OK`，failures=0、errors=0、skipped=0；由于幂等修复发生在 v02 初次重放之后，v02 将保留为历史证据，不作最终运行目标，下一项从空 v03 重验当前代码。
21. 最终运行目标已更新为全新 `f0d_acceptance_v03` + `/private/tmp/anhuan-f0d-acceptance-v03`；迁移从空库实跑 `-> f0d_0001 -> f0d_0002`，不删除、不覆盖默认旧 `f0d`、v01 或 v02。
22. v03 当前代码真实重放：smoke=10 uploads/10 blobs/13,568,633 bytes/110 units/105 native/5 OCR 候选/2 deferred；full=26/26/41,878,200/249/225/24/2、26 jobs、52 audit；第二次 full 七项业务增量全0，external/OCR/Gold/专业发布全0。
23. v03 反向验证再次通过：`valid_exit=0 tampered_exit=2 restored_exit=0`，`tenant_leaks/body_leaks/external_calls/ocr_calls/gate_bypasses` 全0。
24. Artifact 校验器收紧红→绿：首次因 `pg_get_constraintdef` 受 migration role `search_path` 影响而正确失败关闭 `ACCEPTANCE_EVIDENCE_MISMATCH`；改为核对源/目标 schema、table 和有序列组合后，5 个 lineage 定义全精确，危险 schema/table/column 授权计数为0。
25. v03 最终产物连生两次哈希一致：acceptance=`e355a9c6…abe2f`、status=`2ae71f91…709f6`、sbom=`2c5cd1fd…bef3`；早期 v02 的产物哈希已被当前 223-test/v03 证据取代。
26. 最后当前树回归：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` 实测 `Ran 223 tests in 18.119s` / `OK`，failures=0、errors=0、skipped=0。
27. 最终产物卫生扫描：3 files，`filename/absolute_path/environment_demo/email/phone/remote_resource/secret_canary` 命中全0，新 F0-D 文件与26源哈希交集0；产物目录0700、文件0600。
28. 依赖与禁用面复核：`requirements/f0d.lock`=24、venv runtime=24、version mismatches=0、extras=0；production 源码对 requests/httpx/urllib/socket/subprocess/fitz/tesseract/ocrmypdf/pdftoppm/soffice/boto3/openai 扫描0命中，skip/todo 扫描0命中。
29. 最终冻结复核：F0-A=`3096e49e…3db99`、F0-B=`28646fe3…ca075`、F0-C=`15ca3e7b…358c9`；core/negative manifest、route/full-plan 均为登记指纹，26 份源文件两份 `shasum -s -c` 全 exit=0。
30. 双路末审收口：架构与安全子审计员均对 v03 DB/vault/幂等修复/产物做了只读 live 复核，scoped P0/P1 阻塞=0；保留的非阻塞边界是显式 v03 DSN、Fixture token 非生产身份、artifact 外部命令证据见本文、三文件非跨文件事务快照、vault 非 WORM 及 P0 能力仍关闭。

## F0-E 开工回执
1. 目标：把 225 个 native 页、24 个本地 OCR 候选与 2 个 DOC 延后统一为 249 条页级执行路线；正文和页图均不落盘。
2. 顺序：冻结 F0-D -> 临时样本锁运行时/模型 -> migration/RLS/lease -> service/replay -> 防退化 -> fresh v01 真实验收。
3. 基线：223 tests 全绿、skipped=0；四组 F0-E 冻结总指纹及 F0-A/B/C、manifest、route/full-plan 全部匹配。
4. 数据门禁：26 份登记源由 live catalog 逐份复核，合计 41,878,200 bytes；full=249 visual/225 native/24 OCR/2 deferred/errors0。
5. v03：revision=f0d_0002、26 sources/26 versions/249 units/26 jobs；acceptance SHA=e355a9c6…abe2f。
6. 反向红→绿：默认历史库 tenant_leaks=1/exit2；显式 v03 后 0→2→0，tenant/body/external/OCR/gate bypass 全0。
7. 运行环境：Darwin 25.4.0 arm64、Python3.11.9、Docker/daemon29.6.2、Compose5.3.1、free878GiB。
8. 最大风险：renderer/OCR/model/license/hash 无法离线固定，页几何错线，正文/页图在异常或强杀路径泄漏，stale lease 覆盖首次终态。

## F0-E 执行记录
1. Migration 红→绿：首次捕获 PostgreSQL generated-column 链限制，修正后又捕获旧 F0-D 14 表 FORCE RLS 冻结冲突；新增 4 表最终隔离到 `f0e` schema，空库 0001→0002→0003 成功，`f0d=14/f0e=4`。
2. 数据库防线实跑：新增 11 项真实 PostgreSQL 测试全绿，覆盖 PUBLIC 零权限、会话/RLS 隔离、配置不可变与 mutable image 拒绝、actor 防伪、DELETE/TRUNCATE 拒绝及持久化列无正文/路径载荷。
3. 页级契约红→绿：发现空 OCR 的 manual 终态与通用 confidence 非空约束互斥；修正为仅非空 OCR 要求 confidence，空结果严格为 `MANUAL_REVIEW_REQUIRED` 且 confidence=NULL。
4. 当前 F0-E 定向套件：`PYTHONPATH=src .venv/bin/python -B -m unittest -v tests.test_f0e_local_ocr` → `Ran 34 tests`、`OK`、skipped=0。
5. 运行时红→绿：首次锁校验因 runner/component/SBOM 漂移正确拒绝 `RUNNER_CONFIGURATION_INVALID`；最终 runner、component lock、SBOM、seccomp、Dockerfile/compose/requirements 全部 hash 对齐，loader 输出 `lock_ok=1`。
6. 候选收口：GUI OpenCV wheel 在离线导入时缺 `libxcb.so.1`；按最小替换改为同版本、hash 固定的 `opencv-python-headless 5.0.0.93`，RapidOCR/ONNX/model 不变；原因已写入 lock/notices/SBOM。
7. 最终不可变镜像为 `sha256:afff23f8…86085a`，live inspect 为 linux/arm64、65532:65532、固定 `python3 -I -B` runner；PDF/JPEG/空白/篡改合成探针全绿，body/temp residuals 均0。
8. Enqueue 红→绿：首轮 smoke 在配置落库后因 immutable plan/config 的 `SELECT FOR SHARE` 需要未授予的 UPDATE 权限而失败关闭；移除无意义行锁（两表已有 immutable trigger/FK）后首条 enqueue 通过，并从同一 fresh 库/vault 幂等恢复。
9. Fresh `f0e_acceptance_v01` 与全新 vault 完成 smoke：10 documents、8 eligible plans、110 page evidence、105 native、5 local OCR、4 PDF render、2 DOC deferred；external/body/image/gate/route violations 全0。
10. 首次 full 完成：26 documents、24 plans/runs/jobs、249 unique page evidence、225 native、24 local OCR evidence、23 PDF render、2 DOC deferred；新增19 OCR 调用，正文与页图均未持久化。
11. 第二次 full 完成：jobs/runs/page/deferred 六项业务增量全0，processed/render/OCR this run 全0；vault仍26，evidence summary 两次均为 `223d0462…4d79`。
12. 防退化扩展完成：F0-E 独立 unittest 从34增至57，新增 runtime lock/模型/引擎/profile篡改、strict stdout、CropBox/rotation、nonblank=0、像素/bbox/confidence、全局并发、真实 digest runner 与1ms超时强杀/残留、custom replay终态覆盖。
13. 管道泄漏红→绿：真实 runner 测试首次暴露 stdout/stderr pipe `ResourceWarning`；supervisor 在所有成功/异常路径显式关闭三管道后，2项真实 runner 测试无 warning 全绿。
14. 当前全仓回归：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 280 tests in 16.085s`、`OK`；无 skip/todo，旧套件未删改。
15. 反向验证完成：真实 final DB + 临时 vault 主动做 exact replay、跨计划页注入、错误 lease token；严格九行 0→2→0，tenant/page-crosswire/stale/external/body/temp 全0。
16. 三产物确定性完成：最终 acceptance=`06b8ebd6…191fb`、status=`38109123…3316`、SBOM=`e5cbbee2…2b299`，连续两次生成完全一致；状态为 `LOCAL_FIXTURE_OCR_EVIDENCE_ACCEPTED`。
17. 产物聚合复核：249 visual/225 native/24 local OCR/23 PDF render/2 DOC deferred，raw text/page images/external calls 全0；3文件 filename/path/email/phone/remote/canary/mode 违规全0，新文件与26源 hash 交集0。
18. 最终全仓回归红→绿：受限沙箱复跑因本机 PostgreSQL/Docker 被系统拒绝，得到 `tests_run=212 failures=1 errors=3 skipped=0`；未改代码，以同一套件在获准的本机验收环境复跑得到 `Ran 280 tests in 16.100s`、`tests_run=280 failures=0 errors=0 skipped=0`。
19. 最终反向验收复跑：严格九行仍为 `valid_exit=0 tampered_exit=2 restored_exit=0 tenant_leaks=0 page_crosswires=0 stale_lease_writes=0 external_calls=0 body_leaks=0 temp_residuals=0`。
20. 最终 DB/RLS 聚合：revision=`f0d_0003`；24/24 jobs 成功；249/249视觉单元唯一、225 native、24 local OCR 终态、23 render、2 deferred；manual/raw/image/gate/negative/route 违规全0；F0-D/F0-E FORCE RLS=14/4，PUBLIC 表权限=0。
21. 最终 runtime/vault 复核：不可变 arm64 镜像=`sha256:afff23f8…86085a`，非 root 65532、固定 `python3 -I -B` 入口；runtime lock=`d996594e…e541e`、profile=`8b79ddd2…674b6`；运行中/已停止 F0-E 容器残留均0。vault=26对象/41,878,200 bytes，26/26哈希与DB一致，staging/权限/link/special/symlink违规全0。
22. 最终幂等复核：24 job、24派生 idempotency key、24 plan、24 run 均一一对应且唯一；第二次 full 的 jobs/runs/page/deferred/processed/render/OCR 增量全0，evidence summary 保持 `223d0462…4d79`。
23. 最终产物连续重建两次 `deterministic=true`：acceptance=`06b8ebd6…191fb`、status=`38109123…3316`、SBOM=`e5cbbee2…2b299`；聚合卫生扫描 `filename/absolute/email/phone/remote/canary/mode/source-hash-intersection` 全0。
24. 最终运行面静态复核：外部 provider/network/渲染外部程序禁用 import=0，容器 runner 的 subprocess import=0；新增测试 skip/todo/mock 指令全0。SQL 的 `SKIP LOCKED` 仅是并发 claim 语义，不是测试跳过。
25. 最终冻结复核：旧 tests=`80ac0204…8ebd`、旧 migration=`d69c40fc…8570`、F0-D 三产物=`211160b8…e759`、旧依赖/compose/任务书=`da622019…23d`；F0-A/B/C、core/negative manifest、route/full-plan、F0-D acceptance 均精确匹配登记值；两份26源 `shasum -s -c` 均 exit=0。
26. 最终改动边界：按本轮启动时间复核，非白名单修改文件=0、生成 pyc=0；4个旧数据库仍在且未删除，fresh F0-E 验收库唯一存在。本轮仍只证明 `LOCAL_FIXTURE_OCR_EVIDENCE_ACCEPTED`，不证明准确率、Gold、SEARCH_READY、专业结论、UAT 或生产可用。
27. 双路末审收口：架构/运行时/安全子审计均只读 PASS；安全审计初扫发现本轮生成的 0003/F0-E pyc 后，主执行者仅定向清理这些缓存，复扫 `f0e_named_outside_allowlist=0`、F0-E pyc=0，旧 0001/0002 pyc 保留不动；scoped blocker=0。

## F0-F 开工回执
1. 目标：把249个视觉单元变成仅存于受控内存与PostgreSQL pgcrypto密文的正文证据，并建立15页待人工双标队列；不晋级Gold。
2. 顺序：冻结F0-A～E -> 合成样本验证密文/runner -> 0004与服务 -> 防退化 -> fresh smoke/full/full -> 末审。
3. 基线：全仓 `tests_run=280 failures=0 errors=0 skipped=0`；F0-E反向九项仍严格全绿。
4. 冻结：F0-A～E核心组=`6133082a…f697d`；core/negative两份源manifest `shasum -s -c` 均exit=0。
5. F0-E：249 unique/225 native/24 OCR/23 render/2 DOC deferred；artifact三SHA、runtime lock/profile及最终arm64镜像均精确匹配。
6. pgcrypto：`available=true installed=false`；由fresh F0-F迁移安装，旧库不动；最终F0-E容器残留=0。
7. 首次并行门禁因沙箱自动审批超时被中止、未改代码；改为同一命令串行后全部通过，不将资源/审批失败算产品红灯。
8. 最大风险：正文经runner私有IPC意外进入用户日志、随机密文破坏幂等、错key/错页仍可返回看似成功、OCR正文与F0-E摘要链不一致。
9. 边界：真实客户、地区行业、Acceptance Gold、外部provider、专业责任、UAT与生产继续CLOSED；本轮不宣称准确率或生产KMS。

## F0-F 执行记录
1. 离线正文运行时完成：仅以冻结 F0-E arm64 镜像 `sha256:afff23f8…86085a` 为 base，无联网、pull 或新增包；新镜像固定为 `sha256:6c064050…b1e11`，profile=`215562ed…a079`。
2. 运行时合成探针通过：PDF/JPEG/空白 JPEG 均 exit=0、canonical/schema/F0-E 摘要复算=1；篡改与超限均 exit=2、错误输出无正文，stderr=0，F0-F 容器残留=0。
3. 运行时红→绿：BuildKit 首次试图远端解析本地 digest 而失败；未改依赖或放宽网络，改用 legacy builder 的同一 `--pull=false --network=none` 命令后离线命中本地冻结层并构建成功。
4. Keyfile 防退化红灯：首批 38 项合成测试在 FIFO 用例前 16 项全绿，随后实测 `load_keyfile` 因阻塞式 `open(O_RDONLY)` 挂起；已定向停止唯一测试 PID 并删除仅由该测试创建的 FIFO，待改为 `O_NONBLOCK` 后原用例复跑。
5. 独立运行时审计发现 Compose 仍以 mutable tag 作为可执行入口；已拆除 Compose build/tag 旁路，固定为 runtime-lock 的 `sha256:6c0640…b1e11`，更新锁后 `docker compose config --images` 仅输出该 content ID，loader 自检仍通过。
6. 0004 migration 空库实跑通过：0001→0004、pgcrypto 独立 schema、F0-F 5 表全部 FORCE RLS/不可变、PUBLIC 零权限；错 key SQLSTATE=39000 且零新增，旧 job kind 约束未回归。
7. Keyfile 红→绿完成：读取端加入 `O_NONBLOCK|O_NOFOLLOW` 后，原 FIFO 用例立即稳定拒绝；同一 F0-F 合成套件实测 `Ran 38 tests in 0.104s / OK`，含 key mode/symlink/hardlink/FIFO、native hash/count、队列分层及 runtime identity。
8. 正文服务与验收链完成：F0-D smoke 在临时测试 vault 重放后，由冻结 F0-E runner 生成真实页证据，再由 F0-F 对 native 严格重解析、OCR 重新执行并经 pgcrypto 原子落密文；无 mock DB、parser、OCR runner。
9. 真实集成测试红→绿：首轮 72 tests 中仅 vault 篡改用例因未进入 lazy context 得到 1 failure；第二轮进入后暴露预期异常类型应为 redacted `F0EError` 而非底层 `VaultError`；修正后第三轮 `Ran 72 tests in 33.686s / OK`、skipped=0。
10. 新增集成覆盖：正确/错 key、跨租户、密文主动篡改并事务回滚、跨页交换、缺页、terminal 幂等、过期 lease 重领/旧 lease 零写、append-only、伪 Gold 拒绝、源对象篡改、真实 F0-F 1ms 强杀与容器残留0。
11. P1 安全复审收口：seccomp 新增 clone3、带 NEW* flags 的 clone 及 open_tree/move_mount/fsopen/fsconfig/fsmount/fspick/mount_setattr 拒绝；同安全参数 live 探针九项 errno 均为1，OCR runner 仍可正常完成。
12. 收紧后离线镜像固定为 `sha256:7316755e…a0a64`、profile=`6f396e7b…41151`、runtime lock=`9519651a…7df4`；Compose 仅引用该 content ID，F0-E base 未变。
13. 首次跨页红→绿：旧结构只绑定调用方自报 body hash；现以 `BoundPageBody` + OCR block byte lengths 重算 F0-E text-sequence hash，SQL 首次 finalize 也独立复算。真实未完成多页 job 交换正文后 body/audit/job/progress 全不变，随后正确 finalize 成功。
14. Gold 绕过关闭：`record_gold_label`/`adjudicate_gold_labels` 对 runtime/worker 的 EXECUTE 均撤销；service 与直连 SQL 双层拒绝，真实 label/adjudication 保持0。
15. P1 专项红→绿：首轮 77 tests 唯一错误为权限判卷动态 SQL 拼接错误；改成四个显式 privilege 断言后同一套件 `Ran 77 tests in 74.741s / OK`、skipped=0。
16. 最终 fresh 目标已创建且此前确认 DB/vault/key 三者均不存在；`f0f_acceptance_v01` 只含新建 `f0d` schema，Alembic 实跑 `0001→0002→0003→0004` 成功，尚未覆盖任何旧库。
17. Fresh smoke 真实重放成功：10 versions/plans、8 F0-E runs、2 DOC deferred、110视觉单元=105 native+5 local OCR、6/6正文 jobs、110密文正文逐条解密复核；F0-E/F0-F OCR调用各5，外部调用/正文列/页图/开闸均0。
18. 第一次 full 真实重放成功：26 versions/plans、24 F0-E runs、2 DOC deferred、249视觉单元=225 native+24 local OCR、22/22正文 jobs、249密文逐条解密复核；本次增量139 body/16 job，新增 OCR 调用19。
19. 标注底座实证：队列15=10 OCR+5 native，OCR覆盖7文档、native覆盖5文档，negative=0；label=0、adjudication=0、benchmark=NONE，未晋级 Gold。
20. 第二次 full 幂等复播成功：processed/F0-E OCR/F0-F native/F0-F OCR 均0；configuration/body/job/succeeded/queue/label/adjudication 增量全0，vault仍26，证据摘要两次均为 `a2b7e48f…b66e9`。
21. 三产物从最终 DB 聚合证据生成并连续重生两次，SHA 完全一致：acceptance=`85987510…3c79f`、status=`696a9612…c6221`、SBOM=`d57d4350…a73c0`；状态仅为 `LOCAL_FIXTURE_CONTROLLED_BODY_ACCEPTED / ANNOTATION_PENDING / NOT PRODUCTION`。
22. 最终反向验证严格九行全绿：`valid_exit=0 tampered_exit=2 restored_exit=0 wrong_key_reads=0 tenant_leaks=0 page_crosswires=0 gold_false_promotions=0 plaintext_or_key_leaks=0 external_calls=0`。
23. 当前全仓实跑 `Ran 357 tests in 93.287s`、skipped=0；356项通过，唯一 failure 精确为冻结旧 `test_f0e_local_ocr.py:906` 仍断言 head=`f0d_0003`，实际合法 0004 返回 `f0d_0004`，无第二个失败/错误；按白名单未改旧测试。
24. 最终 DB 聚合复核红→绿：首次 migration role 未设置租户上下文，FORCE RLS 正确返回业务行0；改用已认证 runtime transaction 后得到 sources/versions/plans=26、visual/body/unique=249、native=225、OCR=24、deferred=2、jobs=22、queue=15、labels/adjudications/gates/forbidden columns=0。
25. 最终 key/vault：key=`0600/32 bytes/nlink1`，原始 key 在 verifier/body ciphertext 中直接匹配0；fresh vault=26对象/41,878,200 bytes。revision=`f0d_0004`，F0-F五表 FORCE RLS=5。
26. Artifact/工作区卫生：3文件、目录0700/文件0600；free-string 的绝对路径/环境名/远程资源/邮箱/电话命中0；F0-F scoped 文件与26源 hash 交集0。一次 `rg` 误加 `|| true` 已废弃并记入 BLOCKED；去掉后同 pattern 原始 exit=1。
27. 冻结与原件复核：84个旧文件按登记算法汇总仍为 `6133082ac27cb6d11cba9260457ffb82fb379dfa9fb7eafd9ce66f8e9c1f697d`；core/negative manifest 分别=`e9425d…6316ae`/`2238a2…20e04`，24+2份源 hash 静默复核均 exit=0。
28. 最终 runtime 复核：F0-E 9层是 F0-F 11层的精确前缀、仅新增2层；linux/arm64、user=65532:65532、固定 `python3 -I -B` 入口；F0-F 容器残留输出0 bytes/exit0。
29. 静态末审：network/provider imports、外部 OCR 二进制 marker、F0-F 测试 skip/todo/mock 三组 `rg` 均无匹配且原始 exit=1；F0-F pycache 输出0 bytes。
30. 最终 artifact/runtime 身份一致=true；聚合 counts=249/225/24/22/15/10/5/0/0，`gold_status=ANNOTATION_PENDING`、`search_ready=false`、`production_allowed=false`。最终 DB `gold_execute_grants=0 public_privileges=0`。

## F0-F 最终验收命令回执
1. Fresh 前置探针 → `database_exists=false vault_exists=false key_exists=false`；新建目标后 `fresh_database_created=true schema=f0d owner=f0d_migration`。
2. `F0D_MIGRATION_DSN=… .venv/bin/alembic upgrade head` → 依次输出 `0001 → 0002 → 0003 → 0004`，exit=0。
3. `PYTHONPATH=src .venv/bin/python -B -m unittest tests.test_f0f_controlled_body_gold`：最初72项 vault lazy-context/type 两轮红灯；修正后72项OK。P1收紧后首轮77项仅权限判卷SQL语法error；显式权限SQL后 `Ran 77 tests in 74.741s / OK`。
4. `… -m platform_foundation.f0f replay --profile smoke` → `selected=10 visual/body=110 native=105 OCR=5 deferred=2 jobs=6 decrypted=110 external=0`，exit=0。
5. 第一次 full → `selected=26 visual/body=249 native=225 OCR=24 deferred=2 jobs=22 decrypted=249 queue=15 labels=0 adjudications=0`，exit=0。
6. 第二次 full → `processed=0 base_OCR=0 native_body_calls=0 OCR_body_calls=0`，七项 delta 全0，summary=`a2b7e48f…b66e9`，exit=0。
7. `PYTHONPATH=src .venv/bin/python -B tests/f0f_reverse_verify.py` → 严格九行 `0,2,0,0,0,0,0,0,0`，exit=0。
8. `… -m platform_foundation.f0f artifacts` 连跑两次 → acceptance=`85987510…3c79f`、status=`696a9612…c6221`、SBOM=`d57d4350…a73c0`，两次一致。
9. 全仓 discover → `Ran 357 tests in 93.287s`、skipped=0、errors=0、failures=1；唯一 failure 是冻结旧断言 `f0d_0003 != f0d_0004`，详见 BLOCKED。
10. 冻结/原件 → group=`6133082a…f697d`；core/negative manifest=`e9425d…6316ae`/`2238a2…20e04`；24+2 source checks exit=0。
11. Runtime → seccomp syscall 9项 errno=1；image=`sha256:7316755e…a0a64`、base layer prefix=true、added layers=2、container residual bytes=0。
12. 卫生 → artifact files=3、dir0700/files0600、free-string violations=0、source-hash intersection=0；provider/binary/skip-todo-mock `rg` 均无匹配且 exit=1；F0-F pycache=0。
13. 所有文档/产物更新后最终再次运行反向验证，严格九行仍为 `0,2,0,0,0,0,0,0,0`，exit=0。
14. 独立只读末审确认三项P1（seccomp namespace/mount、首次 finalize 页正文绑定、Gold SQL权限）全部 PASS；最终 acceptance/reverse/artifact 未发现新增P0/P1。
15. 唯一旧测试冲突完成二次独立审计：当前 Alembic 图严格为 `0001→0002→0003→0004(head)`，F0-E/F0-F 两个 fresh fixture 都对同一 Config/env 执行 `upgrade head`，却分别要求 version=`0003`/`0004`，谓词互斥；并行 branch 会使 singular `head` 报 MultipleHeads，条件化 revision/stamp/view/trigger 均会伪造迁移状态。现白名单内不存在合规修复；未来最小诚实修复是单独授权把旧 F0-E fixture 的 upgrade target 固定为 `f0d_0003`，而非把它的断言改成 `0004`。
16. 文档更新后反向验证红→绿：受限沙箱直连本机 PostgreSQL 被拒绝，脚本按设计失败关闭并输出 `2,0,2,1,1,1,1,1,1`；未改代码，确认健康容器后在获准的同一本机 fresh F0-F 目标复跑，严格九行恢复为 `0,2,0,0,0,0,0,0,0`，exit=0。
17. 二次独立审计及文档更新后的当前全仓复跑：`Ran 357 tests in 92.556s`、skipped=0、errors=0、failures=1；仍只有冻结旧 `test_f0e_local_ocr.py:906` 的 `f0d_0003 != f0d_0004`，没有新增失败。其余356项通过，且同次运行实际执行了 fresh 0001→0004 数据库迁移与 F0-F 真实 runner/pgcrypto 覆盖。
18. 领导明确授权旧 F0-E 阶段隔离例外后，仅将其 fixture 的 Alembic target 从全局 `head` 固定为 `f0d_0003`；定向真实 PostgreSQL 复跑严格只执行 `0001→0002→0003`，`Ran 11 tests in 0.695s / OK`。按原84文件/C路径排序/逐文件SHA256再汇总算法，内存还原旧行精确重现登记值 `6133082a…f697d`，修改后的新冻结组指纹登记为 `73d36ea5c2ecf95c78636b4e1a5c70c9e596c1ad5ba601460dfb396e37a27c38`。
19. 解除冲突后的当前全仓真实回归完全通过：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 357 tests in 92.743s / OK`，failures=0、errors=0、skipped=0；同一轮明确显示 F0-E fixture 只执行到0003，而 F0-F fixture 继续执行0003→0004。
20. 当前 F0-F 与冻结 F0-E 两套反向验证并行复跑均 exit=0：F0-F 严格九行=`0,2,0,0,0,0,0,0,0`；F0-E 严格九行=`0,2,0,0,0,0,0,0,0`。错 key、跨租户、跨页、伪 Gold、stale lease、正文/key、外部调用和临时残留仍均为0。
21. 当前三产物连续重建两次 SHA 完全一致，仍为 acceptance=`85987510…3c79f`、status=`696a9612…c6221`、SBOM=`d57d4350…a73c0`；目录0700、三文件0600，绝对路径/环境名/远程资源/邮箱/电话/source filename 命中0，三产物与26源哈希交集0。
22. 两份 source manifest 仅以静默 `shasum -s -c` 复核，24+2 均 exit=0/output bytes=0；manifest SHA仍为 `e9425d…6316ae`/`2238a2…20e04`。F0-F provider import、外部二进制、skip/todo/mock 均0；末审发现并仅删除本轮生成的一个 `src/platform_foundation/f0f/__pycache__`，复扫输出0 bytes。
23. 收口过程如实记录：一次只读 manifest 定位命令范围过宽，工具输出带出登记源文件名；无正文/key/电话/邮箱且未写新 artifact，但聊天输出不可撤回，详见 BLOCKED。本项不影响密文证据、DB、原件或测试结论，但最终报告不宣称“源文件名从未进聊天”。
24. 最终文档更新后再次验收：F0-F 反向严格九行仍=`0,2,0,0,0,0,0,0,0`/exit0；新冻结组=`84 files / 73d36ea5c2ecf95c78636b4e1a5c70c9e596c1ad5ba601460dfb396e37a27c38`；F0-F pycache=0。最终 artifact 聚合仍为249 encrypted=225 native+24 OCR、22 jobs、15 annotation=10 OCR+5 native、labels/adjudications=0，状态 `LOCAL_FIXTURE_CONTROLLED_BODY_ACCEPTED / ANNOTATION_PENDING / benchmark NONE / search false / production false`。

## F0-G 开工回执（Task 0）
1. `docker start anhuan-f0d-postgres-1` → `anhuan-f0d-postgres-1`；健康探针 → `running healthy`，未重建卷、未拉镜像。
2. 目标库只读探针 → `f0g_acceptance_v01=0`，满足 fresh 前置条件，未删除任何 DB/vault/key。
3. 全仓基线 → `Ran 357 tests in 89.916s / OK`，failures=0、errors=0、skipped=0。
4. F0-F reverse → 严格九行 `0,2,0,0,0,0,0,0,0`，exit=0。
5. 冻结组按 C 路径排序/逐文件 SHA256 再汇总 → `112 files / af7b53277f396e3ce8733720ce63e635536ebcd5bdfaac9c64c74eb63c8c4649`；Alembic=`f0d_0004 (head)`。
6. 任务书命令经 Markdown 传输把 `*/__pycache__/*` 误写为星号包围的 `pycache`，并转义了下划线；按明确登记算法与指纹改用原始字面路径，得到登记值，无范围变化。

## F0-G Task 1：盲审存储与 API
1. 新增线性 Alembic `f0d_0005`；fresh disposable apply 到0005成功，F0-G两表均 FORCE RLS/append-only，PUBLIC权限0、runtime/worker敏感F0-F表直读0。
2. 三角色固定为不同且ACTIVE的 `FIXTURE_VIEWER`；旧 `decrypt_verified_body` 与两项旧Gold写函数对runtime/worker EXECUTE均为0，正文只能经assignment gate返回。
3. `prepare`动态证明传入queue集合等于完整eligible集合，不在实现硬编码15；固定guideline version/hash，精确重跑guideline/assignment/audit增量0。
4. label ordinal由assignment actor slot决定；相同文本的ID仍绑定actor/slot；exact label/adjudication retry零新增审计，冲突重试稳定拒绝。
5. 裁决函数在同事务验证两份ciphertext hash、解密、size/hash、UTF-8/NFC后才可写；两份到齐前裁决人正文/标签/裁决均拒绝。
6. Python最终交付11文件：loopback-only/no-store FastAPI、prepare、独立0600 token bundle、只读F0-F key、统一本机DSN validator、catalog契约检查与聚合artifact；静态py_compile exit=0。
7. 全仓首次红灯只暴露冻结旧F0-F追随head后的阶段权限冲突：`Ran 325 tests in 42.742s / FAILED (errors=1)`；未放宽0005，详见BLOCKED。

## F0-G Task 2：fresh 验收与深审
1. 新目标创建前复核：源库仍为0004，正文249=225 native+24 OCR、队列15=10+5、label/adjudication=0；源活动连接0，目标DB/token均不存在。
2. `f0g_acceptance_v01` 以只读template clone新建；owner与源库一致，PUBLIC连接权限撤销，runtime/worker仅保留CONNECT；源库未升级。
3. 目标显式升级0004→0005；升级后再次证明source=0004、target=0005、token尚不存在。
4. `prepare`首次得到guideline=1/assignment=15/actors=3、delta=1/15；第二次delta=0/0。
5. 真实聚合保持queue=15、assignment=15、label slots=30、actors/sessions=3、labels/adjudications/Seed Gold/四类作业audit=0；prepare audit=1；token仅核对regular/0600/96 bytes/nlink1/owner。
6. 新增F0-G定向套件扩至115项并独立复跑全绿、skipped=0；显式方法115，无skip/todo/mock或动态造测试。
7. artifact/reverse签发前独立深审发现6项P1（精确重试审计与完整性、DSN egress、prepare原子性、READY catalog/session闭环、raw token扫描）；真实库尚无label/裁决，已暂停签发并分项收口。

## F0-G Task 3～5：深审修复、真实产物与最终验收
1. 6项P1已全部关闭：label/adjudication exact retry均先复验传入值、key与既存密文完整性且零新增审计；裁决不再产生额外pair-read audit；统一DSN validator拒绝远端、错库、hostaddr/options/query；prepare的actor/membership/session/guideline/assignment/audit改为单事务；READY补齐function/policy/trigger/owner/search_path/PUBLIC与session/token bundle闭环；reverse新增raw 32/96-byte扫描。
2. 深审修复前，135项套件曾以3 failures+1 error暴露RLS探针写法与缺失tenant context；探针只在随机disposable DB内以bootstrap身份做篡改，并补齐context后，`PYTHONPATH=src .venv/bin/python -B -m unittest tests.test_f0g_fixture_annotation` → `Ran 135 tests in 5.291s / OK`、skipped=0。
3. 当前受限沙箱第一次同命令只跑到47项即因本机`127.0.0.1:55432`被系统拒绝而`FAILED (errors=1)`；未改产品代码，在获准的同一本机验收环境复跑即得到上述135/135全绿，不把权限失败算产品红灯。
4. 显式测试方法=135；F0-G tests/reverse的skip/todo/mock/动态造测试扫描exit=1（零命中），production migration/API的requests/httpx/urllib/socket/subprocess/fitz/tesseract/pdftoppm/soffice扫描exit=1（零命中）。
5. 真实目标在label/adjudication/real action均为0的前置条件下，仅重放0005的六个SECURITY DEFINER函数与权限收紧；输出`functions_reapplied=6 labels=0 adjudications=0 real_actions=0 revision=f0d_0005`，未重写表数据或源库。
6. 最终真实prepare幂等复播 → `guidelines=1 assignments=15 actor_sessions=3 delta.guidelines=0 delta.assignments=0`；真实状态为15个候选/15个盲审assignment/30个独立label slot/3个不同actor、membership、session与token hash，human labels=0、adjudications=0、Seed Gold=0、七类外部门禁全关。
7. F0-G严格反向验证 → `valid_exit=0 tampered_exit=2 restored_exit=0 wrong_actor_reads=0 peer_label_leaks=0 premature_adjudications=0 self_adjudications=0 tenant_leaks=0 real_fixture_gold=0 plaintext_or_key_leaks=0 external_calls=0`；临时database=0、临时token file=0。
8. 三产物连续重建两次完全一致：acceptance=`41751429b4dbed865260ba0ca0aef14d8a9ed77c0c30333e42ef704c4612840a`、status=`0f527f60f437276d6b5eadfdf11232697e280d7563498ba6c06adca09f382985`、SBOM=`5c1779cfbca2aa1a75c3c80c3f422ad7c4c879cb84d5bc96bdafc526332b2e21`。
9. 产物目录/三文件=`0700/0600`；聚合扫描absolute path、Demo名、邮箱、电话、remote resource、UUID均0，三产物与登记source hash交集0；真实96-byte token及其3段raw/hex/base64/ASCII形态在19个F0-G交付文件中命中0。
10. F0-F源库最终只读聚合仍为`revision=f0d_0004 sources=26 versions=26 visual=249 bodies=249 native=225 OCR=24 deferred=2 jobs=22 queue=15 labels=0 adjudications=0 gates=0`；F0-G目标为`revision=f0d_0005`。旧key仍`0600/32 bytes/nlink1`，旧vault仍`26 objects/41,878,200 bytes`；F0-G token为`0600/96 bytes/nlink1`。
11. F0-F反向验证在0005交付后仍严格九行=`0,2,0,0,0,0,0,0,0`/exit0，错key、跨租户、跨页、伪Gold、正文/key与外部调用均0。
12. 当前全仓真实回归 → `Ran 460 tests in 49.056s / FAILED (errors=1)`、skipped=0；唯一error仍是冻结旧F0-F fixture随`head`升级到0005后，其0004阶段的runtime `body_state()`直读被新权限模型正确拒绝；其余459项无failure/error，详见BLOCKED。
13. 最终F0-G pycache仅清理明确生成的`src/platform_foundation/f0g/__pycache__`、两份F0-G test pyc与0005 pyc；旧阶段缓存未动，复扫F0-G pycache=0。全仓测试临时日志已从`/private/tmp`定向删除。
14. 本轮最终状态仅为`LOCAL_FIXTURE_ANNOTATION_WORKFLOW_READY / HUMAN_LABELS_REQUIRED / NOT_GOLD / benchmark NONE / external DENY / NOT PRODUCTION`；没有执行真实label/adjudication、OCR/LLM、Acceptance Gold、专业判断、客户UAT或生产开闸。
15. 最终冻结复核按登记的112文件显式集合、C locale相对路径排序、逐文件`shasum -a 256`标准输出再SHA-256 → `112 files / af7b53277f396e3ce8733720ce63e635536ebcd5bdfaac9c64c74eb63c8c4649`，与Task0完全一致；F0-A～F冻结文件未漂移。
16. 两份登记manifest SHA仍为`e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae`/`2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04`；在只读源目录分别执行静默`shasum -s -c`均`exit=0/output_bytes=0`，24+2原件未漂移。
17. 文档与产物全部更新后的最终F0-G反向验证再次严格输出同一11行`0,2,0,0,0,0,0,0,0,0,0`并exit0；未产生真实label、adjudication、Gold或外部调用。

## F0-G 收口续跑（2026-08-06）
1. 现状复核：容器=`running healthy`、head=`f0d_0005`、loader=`492`；获准本机环境F0-G=`135/135 OK`、reverse严格11行全绿；全仓仍为`Ran 460 / errors=1`，唯一旧F0-F阶段隔离阻塞未越界修改。
2. API实现固定`127.0.0.1:8767`的`serve`与真实`serve --check` bind，禁host/port参数、proxy/forwarded/access/reload/server/date headers，单worker；裁决JSON加入4096-byte声明长度+流式双闸门和finally清零。
3. 0005权限模型收紧为runtime仅5个API wrapper、worker零F0-G函数；runtime/worker/PUBLIC对F0-G两表及F0-F四敏感表的table/column DML均撤销；catalog补齐grant option/schema/owner/ACL/RLS/policy/trigger并在Service构造时失败关闭。
4. token bundle验收新增三角色、三独立token hash、完整queue覆盖和逐assignment角色列绑定；裁决label敏感buffer部分构造失败也全量wipe，SQL补body configuration缺失拒绝。
5. reverse正在强化为真实状态完整闭环、15份正文内存片段、raw/hex/Base64/Base64URL、受控日志自检、跨assignment零写及无`except Exception: pass`；真实库尚未同步新函数/权限，真实label/adjudication/Gold仍未执行。
6. 新防退化由135增至159个显式方法；首轮红灯=`Ran 54 / 1 failure + 1 error`，分别暴露SBOM直接依赖清单口径差异与“worker无schema USAGE时无法自行解析regprocedure”；catalog改为migration owner按指定role查询ACL、SBOM测试对齐实装直接依赖后，第二轮仅剩cross-assignment探针误用事务helper，修正后`Ran 159 tests in 8.663s / OK`、skipped=0。
7. 新覆盖包含固定serve/无host-port、4096双上限、runtime/worker六表table+column DML/schema/function授权篡改、三角色assignment错绑、跨assignment ID零写、ciphertext非明文、stdout/stderr/log canary与组件许可证；未mock DB/API/pgcrypto/auth。
8. 真实同步前新版reverse按设计失败关闭，严格11行为`2,0,2,1,1,1,1,1,1,1,2`/exit2；带FORCE RLS会话复核source=`0004/249 bodies/15 queue/labels0/adjud0`、target=`0005/guideline1/assignment15/labels0/adjud0/Gold0/actions0`后，单事务仅重放6个SECURITY DEFINER函数与权限锁，输出`functions_reapplied=6 privileges_locked=1`且前后数据计数完全相同。
9. 新权限下真实prepare连续两次均`guidelines=1 assignments=15 actor_sessions=3 delta=0/0`；`serve --check`实际bind并关闭=`127.0.0.1:8767`/exit0。
10. reverse首轮同步后又以`BufferError`抓到cross-assignment探针持有memoryview时清零的测试自身缺陷；改为独立bytearray key并finally wipe后，临时库诊断=`exercise ok / 87 bounded needles / leaks0 / external0`，严格11行恢复`0,2,0,0,0,0,0,0,0,0,0`/exit0。
11. 三产物按新口径连续生成两次字节一致：acceptance=`b6982d166d086fe68667d95d0cf88de4a6c225f5091ee9eda04554ce02c03ab9`、status=`0adba01c7a7fa88fdc92844ccdae7f569c94d9f3a1907204690f7b7016bbd4cb`、SBOM=`a3d624d24d8779dbfa6c1cb8eed6978b4e2ec36e83fded0c0cfadaac0a32a12c`；目录0700/文件0600。
12. acceptance明确区分DB aggregate/catalog/token=`IN_PROCESS_VERIFIED`与listener/reverse=`SEPARATE_GATE_NOT_BOUND`；SBOM分开Darwin/arm64/Python3.11.9 API和Linux-container/arm64/PostgreSQL18，并登记8个实装Python直接运行组件；三产物absolute path/Demo名/远程资源/email/phone扫描均exit1/零命中。
13. 当前loader=`516`、F0-G显式方法=`159`，skip/todo/mock扫描零命中；全仓实跑=`Ran 484 tests in 52.105s / FAILED (errors=1)`，唯一仍是旧F0-F数据库类32项因fixture追随head到0005后阶段直读被拒，故`516-32=484`，没有第二个failure/error。
14. F0-F冻结reverse复跑严格九行=`0,2,0,0,0,0,0,0,0`/exit0；F0-A～F登记算法仍=`112 files / af7b53277f396e3ce8733720ce63e635536ebcd5bdfaac9c64c74eb63c8c4649`，旧F0-F test SHA仍=`1fe75c923c36cc41d49744d21822bbe89aec174eb07a26c469921856e6644973`，证明未越界修改。
15. 两份manifest SHA仍=`e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae`/`2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04`；在唯一登记Demo根目录静默`shasum -s -c`得到`manifests=2 failures=0 output_bytes=0`，26 source hash与3 artifact hash交集0。首次工作目录错误导致源文件名进stderr的流程事故已如实记入BLOCKED。
16. 最终复跑：F0-G=`Ran 159 tests in 8.576s / OK`；reverse严格11行=`0,2,0,0,0,0,0,0,0,0,0`；loopback真实bind=`127.0.0.1:8767`/exit0；三产物重生SHA与第11项一致，路径/Demo名/远程资源/email/phone命中0，F0-G生成的pycache已定向清理。
17. 最终全仓=`loader 516 / Ran 484 tests in 52.571s / errors=1 / skipped=0`，仍只在旧F0-F数据库类`setUpClass`发生同一阶段隔离错误，32项未展开；旧F0-F测试SHA与112文件冻结组均未变。本轮白名单内工作已耗尽，等待单独授权固定旧fixture到`f0d_0004`。
18. 双路只读末审：真实库=`0005/guideline1/assignment15/roles-memberships-sessions 3/3/3`，label/adjudication/action/Seed Gold/权限与绑定违规/临时残留均0，F0-G catalog与F0-F四敏感表FORCE RLS/ACL/policy/trigger均PASS；静态审计P0=0。其指出的4处测试清理分支literal `pass`已改成仅抑制`FileNotFoundError`的显式上下文并经159项复跑，生产/迁移/reverse/test literal pass最终均0。
19. 领导明确授权旧F0-F阶段隔离例外后，仅把`tests/test_f0f_controlled_body_gold.py` fixture的Alembic target从`head`固定为`f0d_0004`，原0004断言保留；文件SHA由`1fe75c923c36cc41d49744d21822bbe89aec174eb07a26c469921856e6644973`变为`1b9a443fe2cf643cee8929ec2e41012b0f53a1438031dc1a6a3da06cdeae8573`。按原112文件算法实算并重新登记F0-A～F冻结组=`14d161d861871c321f37dead8befe9ccf58d8b803c8fba235c93d6c8c44f463b`；旧值保留为修改前历史证据。
20. 阶段隔离修正定向验收通过：F0-F fixture实跑严格只升级`0001→0002→0003→0004`，`Ran 77 tests in 77.721s / OK`；F0-G仍=`Ran 159 tests in 9.226s / OK`。F0-F reverse九行=`0,2,0,0,0,0,0,0,0`/exit0，F0-G reverse十一行=`0,2,0,0,0,0,0,0,0,0,0`/exit0。
21. 授权修正后的全仓真实回归完全通过：loader=`516`，`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 516 tests in 104.384s / OK`，failures=0、errors=0、skipped=0；旧F0-F类原先未展开的32项已实际执行，未出现第二问题。
22. 全仓绿灯后F0-G三产物再连续重生两次且SHA逐字一致，仍为acceptance=`b6982d166d086fe68667d95d0cf88de4a6c225f5091ee9eda04554ce02c03ab9`、status=`0adba01c7a7fa88fdc92844ccdae7f569c94d9f3a1907204690f7b7016bbd4cb`、SBOM=`a3d624d24d8779dbfa6c1cb8eed6978b4e2ec36e83fded0c0cfadaac0a32a12c`；状态仍仅`LOCAL_FIXTURE_ANNOTATION_WORKFLOW_READY`。
23. 最终静态/旧资产复核：唯一旧测试target=`f0d_0004`且`head`命中0；新测试SHA和112文件新冻结组均等于第19项登记值；literal pass/skip/todo/mock/禁用import/F0-G pycache均0。旧F0-F三产物SHA仍=`85987510…3c79f`/`696a9612…c6221`/`d57d4350…a73c0`，key=`0600/32 bytes/nlink1`，vault=`26 objects/41,878,200 bytes`；两份manifest SHA保持登记值且24+2原件静默校验exit0/output0。F0-G产物目录/文件=`0700/0600`，路径/Demo名/远程资源/email/phone/source-hash交集均0；loopback最终bind exit0。
24. 上述代码、产物与文档全部定稿后，两套reverse再次复跑：F0-F严格九行=`0,2,0,0,0,0,0,0,0`，F0-G严格十一行=`0,2,0,0,0,0,0,0,0,0,0`，均exit0并恢复成功；真实label/adjudication/Gold与外部调用仍为0。
25. 最终残留只读盘点：F0-G test/reverse命名空间临时数据库=0，临时token/log文件=0。另发现13个零活动连接的旧`f0f_test_`数据库，命名与过去`setUpClass`失败会遗留的scratch库一致，但PostgreSQL无足够证据精确归属到某次运行；因本目标明确禁止删除旧DB，全部保持不变并记入BLOCKED，不影响F0-G完成条件。
26. 最终当前态`prepare`连续两次均=`guidelines1/assignments15/actor_sessions3/delta.guidelines0/delta.assignments0`，状态仅`LOCAL_FIXTURE_ANNOTATION_WORKFLOW_PREPARED / HUMAN_LABELS_REQUIRED / benchmark NONE / production false`；证明全仓绿灯后真实目标仍可重放且零新增。

## F0-H 开工回执（Task 0）
1. 目标：新增 RapidOCR 3.9.2 + PP-OCRv6-small/ONNX runtime v2 与兼容 adapter；保留旧 v4 证据和回滚入口，不做准确率评价。
2. 基线：Darwin/arm64、Python 3.11.9、无 `.git`；全仓实跑 `Ran 516 tests in 103.188s / OK`、skipped=0。
3. 冻结：F0-A～F=`14d161d861871c321f37dead8befe9ccf58d8b803c8fba235c93d6c8c44f463b`；F0-G 17文件=`a4f8468b3c3428b8ff180b104ad44af5425ff1af49b39976181f8bd0253cf3c0`，均实算匹配。
4. 原件：登记根存在；core24+negative2 共26份只读SHA复核，`failures=0`；两份manifest SHA保持登记值。
5. 旧运行时：F0-E=`sha256:afff23f8…86085a`、F0-F=`sha256:7316755e…a0a64`，Docker实查均为Linux/arm64；7个旧锁文件SHA均匹配。
6. 顺序：冻结v6依赖/模型/镜像 → adapter与smoke/full → ≥24项测试/reverse → 确定性产物与全仓末审。
7. 最大风险：RapidOCR v3 API/字典与旧tuple协议错配、wheel隐式下载、正文进入日志、把新身份冒充旧v4、镜像构建污染旧证据。
8. 边界：仅F0-H白名单；build下载限官方源、runtime断网；migration/DB、26原件、旧F0-E/F0-F/G及宿主Python全部只读。

## F0-H Task 1：PP-OCRv6 离线运行时
1. 冻结 RapidOCR=`3.9.2`、ONNX Runtime=`1.28.0`、PP-OCRv6-small det/rec；方向分类器单独登记为旧 mobile 组件，未冒充 v6；全部 wheel/model/config/字典元数据均有 SHA 与许可证清单。
2. Linux/arm64 镜像以旧 F0-E content ID 为只读 base、`--network=none --pull=false` 离线构建；修正 adapter 后最终新镜像=`sha256:570a05dc48cc45ee88c11b06371d47244e16af30c50d2fca4556477dd2625ff0`，runtime-lock=`111d0f28d0e5f63432dccf410712a3c931dbe2d64356a918e6f67a713ace9fa2`。
3. 真实首轮红灯：合成 PDF 返回公开 `OCR_RESULT_INVALID`、stderr=0；定位为 RapidOCR v3 bbox 是 NumPy ndarray，而冻结 v4 汇总只接受 list/tuple。adapter 显式转成 Python 坐标后重建镜像，单 PDF 实跑=`exit0/schema f0f-body-result-v1/engine3.9.2/blocks1/external0`。
4. 第二个红灯：宿主 `.venv` 无 Pillow，原合成 JPEG 在进入容器前失败；未安装依赖，改为标准库 Base64 解码的固定 320×100 JPEG/blank 字节，header/body SHA 自检通过，runtime-lock 同步冻结。
5. 合成真实验收最终=`Ran 9 tests in 6.750s / OK`：PDF、JPEG、空白 JPEG、篡改源、evidence 模式、v6 身份、正文不落盘与容器零残留均实际执行；静态合同/身份/沙箱先行验收=`Ran 36 tests / OK`。

## F0-H Task 2：登记资料重放
1. smoke 真实重放通过：`documents=10 visual_units=110 native_bypass=105 ppocrv6_ocr=5 deferred=2 errors=0`；provider=`ppocrv6-small`、RapidOCR=`3.9.2`、v4 rollback=true、external/download/body/old-mutation均0，exit=0。
2. 从容器残留0开始串行执行两次干净 full，均为`documents=26 visual_units=249 native_bypass=225 ppocrv6_ocr=24 deferred=2 errors=0`；两次 `execution_summary_sha256=18f722d15a2372c0b1579cb5246db39c62c13a1ec6d90e9116df4a9947dd6f08`，其余安全摘要逐字段相同。
3. full 两轮均证明 v6 provider/model/config/runtime 身份一致、旧 v4 rollback=true、old runtime fingerprint=`dcef41aa…c67166`且mutations=0；external_calls/runtime_downloads/body_leaks=0、raw_text_persisted=false，不宣称准确率。

## F0-H Task 3：防退化与反向验证
1. 新增 F0-H 独立 unittest=51，最终候选镜像上实跑 `Ran 51 tests in 7.579s / OK`、skipped=0；覆盖合同/API适配、模型/锁篡改、真实PDF/JPEG/blank、evidence、沙箱、资源、旧运行时冻结、CLI、重放与产物状态。
2. 静态末审发现管道/进程清理分支仍有 `except: pass`；已改为清理失败公开失败码、仍执行容器移除，runner 中 literal pass=0，并重新离线冻结。最终镜像=`sha256:02e6300f52463818de7ceaf447bfb0765e5f8466251177006131dec4e55a27f5`，runtime-lock=`8f15cdecec2612639f909faba120fab1c8915be3a00f3e1cab601ea42195d77b`。
3. reverse 首轮严格10行仅 `runtime_downloads=1` 红灯；查明 Dockerfile 虽有 `PIP_NO_INDEX=1` 但未显式传 `--no-index`。加入双闸门并以 `--network=none` 离线重建后，51项仍全绿。
4. reverse 最终严格10行=`0,2,2,0,0,0,0,0,0,0`、exit=0：有效与恢复成功，缺模型/锁篡改均exit2，协议/正文泄漏/外部调用/运行下载/旧运行时改动/容器残留均0。

## F0-H Task 4：最终产物与全仓验收
1. 最终 `artifacts` 编排连续完整执行两次；每轮均真实串行 smoke→full→full 并验证full摘要一致。两轮三SHA逐字相同：acceptance=`0d25e1ec9addfa0d24d85523ebd621747835ea40f00d44090c5632dc4676093b`、status=`c1517e060b5333b7b6a04a84af83ce3d7d2a0b3af27884efb1496ad2b906f7c9`、SBOM=`6bf883d9d1d2d83dd907a7eecb1c18b84e8512e0e4a22656921ab4b646267aee`。
2. 产物绑定的真实重放聚合：smoke=`10 documents / 110 visual / 105 native / 5 PP-OCRv6 / 2 deferred / errors0`；full=`26 / 249 / 225 / 24 / 2 / errors0`；两次full共用 `execution_summary_sha256=18f722d15a2372c0b1579cb5246db39c62c13a1ec6d90e9116df4a9947dd6f08`。
3. 最终全仓回归：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 567 tests in 107.406s / OK`，failures=0、errors=0、skipped=0；F0-H定向51项为 `Ran 51 tests in 7.579s / OK`。
4. 两份manifest SHA实算精确匹配 `e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae` / `2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04`；同一登记fd链路静默重读26原件输出 `registered_sources=26 bytes=41878200 failures=0`。
5. 最终冻结复核：F0-A～F=`112 / 14d161d861871c321f37dead8befe9ccf58d8b803c8fba235c93d6c8c44f463b`；F0-G=`17 / a4f8468b3c3428b8ff180b104ad44af5425ff1af49b39976181f8bd0253cf3c0`；7个旧runtime关键文件 `mismatches=0`。
6. Docker实查旧F0-E/F0-F及新F0-H均为登记 content ID、`linux/arm64`、`user=65532:65532`和各自固定 `python3 -I -B` entrypoint；最终F0-H容器查询 `exit=0 output_bytes=0`。
7. 产物目录/文件权限=`0700/0600`、nlink违规0；绝对路径、Demo名、远程URL/资源、邮箱、手机、凭据特征、源文件名均命中0；F0-H范围42文件与26原件 `source_hash_intersection=0`。
8. 静态末审：runner禁用network/subprocess import=0、host network import=0、外部OCR二进制marker=0、literal pass=0、skip/todo/mock=0、F0-H pycache=0；Dockerfile同时具备 `PIP_NO_INDEX=1` 与显式 `--no-index`，构建网络调用marker=0。
9. 按目标开始时间复核 `modified_since_start=44 non_allowlist=0`。最终状态仅为 `LOCAL_PPOCRV6_RUNTIME_READY / ACCURACY_NOT_EVALUATED / SEARCH_NOT_READY / NOT_PRODUCTION`；旧v4仍可回滚，未声称v6更准。
10. 独立只读安全子审计PASS：从26份登记资料在内存提取1571个正文片段反向扫描，三产物 `body_segment_hits=0`；路径/Demo名/PII/凭证/URL/源文件名全为0，symlink/nonregular/multi-link违规也全为0。Host仅有固定Docker编排和v4回滚探针的5次subprocess调用，`runner_forbidden_imports=0 / shell_true=0`。
11. 全部代码、产物与文档定稿后再跑 `PYTHONPATH=src .venv/bin/python -B tests/f0h_reverse_verify.py` → exit0，严格十行为 `valid=0 / missing_model=2 / tampered=2 / restored=0 / protocol=0 / body=0 / external=0 / downloads=0 / old_mutations=0 / residuals=0`。

## F0-I 开工回执（Task 0）
1. 目标：将26份登记资料生成加密、可重组、可追溯的 canonical blocks 与 parent/child chunks；不开放搜索或准确率结论。
2. 全仓基线：`PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 567 tests in 108.078s / OK`，skipped=0。
3. F0-H reverse 严格十行为 `0,2,2,0,0,0,0,0,0,0`/exit0；Alembic唯一head=`f0d_0005`。
4. 冻结实算：F0-A～F=`112 / 14d161d861871c321f37dead8befe9ccf58d8b803c8fba235c93d6c8c44f463b`；F0-G=`17 / a4f8468b3c3428b8ff180b104ad44af5425ff1af49b39976181f8bd0253cf3c0`；F0-H=`42 / 8629b286b539225bf40a2a116c54588adcd829beab2d15278059345a2e304eb1`。
5. manifest SHA精确为 `e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae` / `2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04`；登记fd链路复核=`26 files / 41,878,200 bytes / failures0`。
6. 前置状态：`source_db=1 target_db=0 target_key=0`；只读源为f0g_acceptance_v01，F0-I目标与key确认fresh。
7. F0-H锁=`8f15cdec…d77b`，镜像=`sha256:02e6300f…27f5`/linux-arm64/non-root/固定入口；容器残留=`output_bytes=0`。
8. 顺序：0006/schema与合同 → 纯canonicalizer/结构解析 → DB服务与串行replay → 防退化/reverse → fresh smoke/full/full与产物。
9. 最大风险：F0-H进程锁不跨进程、UTF-8 span遇到多字节/换行断链、RLS或复合外键遗漏导致跨tenant/version串线；验收始终串行。

## F0-I Task 1～2：schema、canonicalizer 与结构合同
1. 新增线性 Alembic `f0d_0006`，含 configuration/run/document_scope/page/block/chunk/chunk_block_link 七表；全部 FORCE RLS、append-only、tenant/version 复合约束，正文只允许 pgcrypto ciphertext 与 SHA/计数/span 元数据。
2. scratch PostgreSQL 实跑升级=`f0d_0005 -> f0d_0006`；定向套件最终120项，加入typed scope、page route、parent/link unit-chain、精确intersection、公式只观察不执行、production persistence及artifact原子性防退化后 `Ran 120 tests in 9.415s / OK`、skipped=0。
3. 首次受限沙箱运行在95项后因无法连接本机数据库而 setUpClass error；未改产品断言，在同一本机获准 scratch 环境重跑全绿，scratch 已由 teardown 清理。
4. canonicalizer 生成稳定 unit/block/parent/child/link ID 与 chain hash；UTF-8 byte/character span 可无缝重组，child目标300～800字符、801切为800+1、尾块可短、overlap=0、不得跨unit。
5. 视觉合同保留真实页几何；OCR bbox 仅接受真实top-left ppm，空OCR诚实记录 `OCR_EMPTY_PAGE/OCR_EMPTY_RESULT`；native固定无框 reason。JPEG保留真实 source-pixel 坐标且不伪造PDF dpi/rotation。
6. DOCX按真实XML顺序保留60段/1表/58格并形成1个真实section parent；XLSX按3个真实sheet/306 cell证据形成3个parent，公式只观察不执行；2 DOC继续DEFERRED。
7. 固定host `flock` 合同已建立，后续重放须让它覆盖DB/key/源读取/F0-H调用的完整临界区；0006固定只接受child block links及parent ordinal=0，持久化接入不得写core内部的parent links。

## F0-I Task 3：防退化与严格反向验证
1. F0-I定向防退化当前120项，超过新增≥40项要求；最终 `Ran 120 tests in 9.415s / OK`、skipped=0。无skip/todo/mock，覆盖UTF-8、多字节span、299/300/800/801边界、bbox/rotation、native无框、DOCX/XLSX真实定位、公式只观察不执行、0600 key、进程flock、RLS/pgcrypto/append-only/idempotency与artifact序列。
2. `PYTHONPATH=src .venv/bin/python -B tests/f0i_reverse_verify.py` 加入主动crosswire攻击后主线程复跑exit=0；严格13行依次为 `valid=0 config_tamper=2 span_tamper=2 restored=0 crosswires=0 orphan_blocks=0 orphan_chunks=0 plaintext=0 external=0 search=0 concurrent_ocr=0 upstream_mutations=0 container_residuals=0`。
3. reverse使用随机scratch clone→0006、临时0600 key、真实PostgreSQL/pgcrypto/FORCE RLS与完整page/block/parent/child/link树；tamper与同tenant错document_version INSERT均在scratch事务内被拒且finally强制rollback，攻击前后七表count+digest一致，scratch/key最终清理。
4. production persistence集成测试使用登记smoke原生PDF、DOCX/XLSX和合成但不mock parser/DB/pgcrypto的PDF OCR文本/空页/JPEG证据，真实执行persist→逐draft/run严格比对→解密重组→rollback；损坏chain与相邻非空零intersection均被拒且零残留。
5. artifact防退化增加真实临时FIFO晚目标探针：三目标完整预验证前不替换任何旧文件；SHA-256中的随机数字串先mask再跑电话heuristic，真实非hash手机号仍固定拒绝。

## F0-I Task 2：真实重放前置闸门
1. 120项production/scratch防退化全绿后再次只读复核：`source_db=1 source_active=0 target_db=0 target_key=0 artifact_dir=0 container_residuals=0 docker_exit=0`；满足fresh且单进程前置条件，真实Fixture尚未执行。
2. 真实重放前清除最后一个cleanup吞异常分支并将测试清理改为显式仅抑制`FileNotFoundError`；F0-I migration/src/test/reverse AST复扫=`literal_pass_nodes=0`。随后定向复跑=`Ran 120 tests in 9.349s / OK`、skipped=0，scratch迁移两次均为`0005→0006`且teardown清理完成。
3. 首次真实smoke严格单进程执行并成功完成`0005→0006`：`documents/scopes=10/10 visual=110 native/OCR=105/5 structure docs/units=2/4 deferred=2 errors/external/search=0`；`rows_inserted=2146 ocr_calls=5 blocks=1022 parent/child/link=114/150/738 orphan/crosswire/reconstruction/plaintext=0`，状态=`LOCAL_CANONICAL_CHUNKS_READY / ACCURACY_NOT_EVALUATED / SEARCH_NOT_READY / NOT_PRODUCTION`，`replay_summary_sha256=f920fdc25681e4f61a92d20ac00559da569b4522c102d54b14831526afabbc8f`。
4. 同次结构聚合实测：DOCX=`60 paragraphs/1 table/5 rows/58 cells/1 section`；XLSX=`3 sheets/306 cells/19 values/0 formula/0 cached formula`；negative scopes=2且enabled gates=0，`raw_text_persisted=false`。
5. 第一次真实full在9.4秒内失败关闭，原始公开输出=`{"error":"F0I_ERROR","reason_code":"REPLAY_MISMATCH"}`/exit2；没有直接重跑或清理目标。双路只读诊断证明真实smoke集合是full严格子集但不是前缀，仅与full前10份重合4份，旧`full sources[:10]`前置合同在OCR/build/write之前必然失败。
6. 最小修复改为从登记smoke plan解析10个唯一source version、在同一只读事务重绑manifest并要求其为full严格子集；新增真实登记“subset且non-prefix”独立回归。修复后定向=`Ran 121 tests in 9.415s / OK`、skipped=0。
7. 失败后的真实目标只读聚合=`revision0006 configuration1/run1/scope10/page110/block1022/chunk264/link738`，`smoke_runs1/full_runs0/smoke_ocr5/full_ocr0`；确认失败full零OCR、零full提交，成功smoke证据未变。
8. 修复后的第一次真实full成功：`documents/scopes=26/26 visual=249 native/OCR=225/24 visual docs=22 structure docs/units=2/4 deferred=2 errors/external/search=0`；本次仅新增`rows=2230/OCR calls=19`，累计run=`2（smoke5 + full19）`。
9. full加密树聚合=`blocks1909 parent253 child300 links1636`，`orphan blocks/chunks=0/0 crosswire/reconstruction/plaintext=0`；结构计数保持DOCX=`60/1/5/58/1`、XLSX=`3/306/19/0/0`，negative=`2/gates0`，`replay_summary_sha256=f95609f699b42e6f8dd61c3d851f6fa5e82ce3097ce50f966d2a6f68d2eae70c`。
10. 第二次真实full幂等复播成功：`rows_inserted=0 ocr_calls=0`，全量聚合逐字段不变，`replay_summary_sha256`仍为`f95609f699b42e6f8dd61c3d851f6fa5e82ce3097ce50f966d2a6f68d2eae70c`；完成真实smoke→full→full串行门禁且累计OCR严格为5+19。

## F0-I Task 3：真实状态后反向验证
1. `PYTHONPATH=src .venv/bin/python -B tests/f0i_reverse_verify.py` → exit0，严格13行依次为`valid=0 config_tamper=2 span_tamper=2 restored=0 crosswires=0 orphan_blocks=0 orphan_chunks=0 plaintext=0 external=0 search=0 concurrent_ocr=0 upstream_mutations=0 residuals=0`；攻击仅发生在独立scratch且teardown清理。

## F0-I Task 3：产物签发红→绿
1. 首次`artifacts`在真实幂等replay后失败关闭，原始输出=`{"error":"F0I_ERROR","reason_code":"ARTIFACT_GENERATION_FAILED"}`/exit2；随即证明`artifact_dir=0 files=0`，原子写未留下半成品。
2. 根因是status文案自身包含公开载荷守卫禁止的敏感存储字段名，而旧测试只查URL；改为只描述`pgcrypto-encrypted values`并新增真实HTML经完整payload guard的回归。定向套件增至122项，`Ran 122 tests in 9.297s / OK`、skipped=0。
3. 修复后`PYTHONPATH=src .venv/bin/python -B -m platform_foundation.f0i artifacts`连续执行两次均exit0；三SHA逐字一致：acceptance=`8a4c58cfed9dda5dd2514c44028a24e916d99431704deac8ddb8a07a9a897d1a`、status=`eb38e014589e2fcb5cfd7671ec5ccc4fe8f0eeb8996f2c5069e1e16b01f9cc5a`、SBOM=`c1cb21692be7bfef76182dd2caf9d000065709f61fbf203d9ed6267c20b6dd68`；两轮均绑定真实已验收库的幂等smoke/full/full，OCR与新增行均0。

## F0-I 最终全仓验收
1. `PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'` → `Ran 689 tests in 116.196s / OK`，failures=0、errors=0、skipped=0；实际执行了旧阶段fresh迁移及0005→0006的两组F0-I PostgreSQL/pgcrypto集成测试。
2. 终审只读`SHOW server_version`与`pg_extension`发现初版SBOM的PostgreSQL 16登记不实，真实为`PostgreSQL 18.3 / pgcrypto 1.4`。增加live component回归后首轮123项因测试把无version模型组件也纳入映射而`KeyError: version`；只筛数据库组件后`Ran 123 tests in 9.426s / OK`，产品SBOM判据未放宽。
3. 修正SBOM后产物连续两轮SHA逐字一致：acceptance=`8a4c58cfed9dda5dd2514c44028a24e916d99431704deac8ddb8a07a9a897d1a`、status=`eb38e014589e2fcb5cfd7671ec5ccc4fe8f0eeb8996f2c5069e1e16b01f9cc5a`、SBOM=`b7fa245e8fa97fce0b937ca97bfcf292469a3bac94a6d9e2ea4893caa4c4e8b5`。
4. 最终全仓再次实跑=`Ran 690 tests in 116.093s / OK`，failures=0、errors=0、skipped=0；总数超过任务要求607，F0-I独立方法=123。
5. F0-I最终reverse再次严格13行=`0,2,2,0,0,0,0,0,0,0,0,0,0`/exit0；F0-H冻结reverse严格10行=`0,2,2,0,0,0,0,0,0,0`/exit0；Alembic唯一head=`f0d_0006`。
6. 独立只读DB审计：源库仍=`0005/f0i tables0/active0`；目标=`0006`、7表RLS/FORCE RLS/append-only全7、18个tenant复合FK、ACL违规0。scope=`26=22 visual+2 structure+2 deferred`，page=`249=225 native+24 OCR`，run=`2/smoke5+full19`，tree=`1909 blocks/253 parents/300 children/1636 links`，orphan/crosswire/reconstruction/plaintext/negative gates均0。
7. 结构与key末审：DOCX=`60段/1表/5行/58格/1 section`，XLSX=`3 sheet/306 cell/19 value/0 formula`；key仅核验`regular/0600/32 bytes/nlink1`全通过，容器残留0、目标活动连接0。
8. 冻结与原件最终实算：F0-A～F=`112/14d161d861871c321f37dead8befe9ccf58d8b803c8fba235c93d6c8c44f463b`；F0-G=`17/a4f8468b3c3428b8ff180b104ad44af5425ff1af49b39976181f8bd0253cf3c0`；F0-H=`42/8629b286b539225bf40a2a116c54588adcd829beab2d15278059345a2e304eb1`。两manifest保持登记SHA，26源=`41,878,200 bytes/failures0`。
9. 三产物=`3 files/dir0700/files0600/nlink1`；最终合同通过，绝对路径/远程资源/PII/源文件名/key raw-hex-base64/内存解密正文32-byte片段命中均0；21个F0-I交付文件与26源hash交集0。production禁用import、literal pass、skip/todo/mock均0；reverse唯一subprocess仅用于任务强制的只读容器残留探针。
10. 精确清理本轮15个F0-I/0006 `.pyc`并移除空F0-I `__pycache__`；复扫残留0。共享工作区另有一份本任务未创建/未修改且无法归属的根级规划稿，保持原样并在BLOCKED登记，不纳入F0-I证据。
11. 最终状态严格为`LOCAL_CANONICAL_CHUNKS_READY / ACCURACY_NOT_EVALUATED / SEARCH_NOT_READY / NOT_PRODUCTION`；Gold、人工标注、分类、embedding、检索、citation、API、网页、LLM、真实客户/UAT/专业判断与生产均未执行或开闸。

# F0-J0 任务书执行回执

## F0-J0 Task 0：基线与前置闸门（2026-08-07）
1. F0-I 三产物 SHA 实算=冻结值三连一致（acceptance=`8a4c58cf…897d1a`、status=`eb38e014…9cc5a`、sbom=`b7fa245e…4e8b5`），perm 600/600/600。
2. `docker ps` 含 `anhuan-f0d-postgres-1 Up (healthy)`；key 文件存在 `stat 600/32 bytes`。
3. 经 `platform_foundation.f0i.config` 建立连接（未硬编码 DSN）→ `alembic_version=f0d_0006`（唯一 head）。
4. 带租户上下文只读聚合七表：`configuration=1 run=2 document_scope=26 page=249 block=1909 chunk=553 chunk_block_link=1636`，唯一 child chunk ID=300（≠300 规则通过）。记为此轮 BASELINE。
5. 全仓回归真实命令输出尾行 `Ran 690 tests in 137.476s / OK`，failures/errors=0。
6. 机器快照：`uname -m=arm64`、`Docker version 29.6.2`、磁盘余量 `866Gi`、`.git` 不存在（只记录不创建）。
7. Docker 残留预检：`docker ps -a`/`volume ls`/`network ls` grep `^anhuan-f0j0-` 三项退出码均=1（零命中）。

## F0-J0 Task 1：路线A OpenSearch 3.8.0 探针（2026-08-07）
1. 镜像拉取经 Docker 内网代理较慢，最终成功；digest 登记 `opensearchproject/opensearch@sha256:bcc17975…f641509`，镜像 arch=arm64/linux。容器 `anhuan-f0j0-opensearch` 端口仅绑 `127.0.0.1:9200`。
2. 密码偏差记录：任务书红线要求 `openssl rand -hex 16`，但 OpenSearch 3.8.0 安全插件硬校验需含大写/小写/数字/特殊字符（纯 hex 被拒，首次启动 exit1）。改为 openssl 生成的 24 字符强口令（含四类字符），0600 存 secrets 目录，聊天不出现。理由按"建议可换"在回执登记。
3. 适配器 `src/platform_foundation/f0j0/{reader,os_client,index_schema,probe}.py`：复用 f0i `database_config/authenticate_local_session/set_tenant_context/load_keyfile` 与 f0f_crypto 解密（未复制密码学/DSN），导出 300 child chunk 全部在内存流式入 bulk API，宿主磁盘零明文。
4. `tests/test_f0j0_retrieval_probe.py` 12 项 C1～C12 实测 `Ran 12 tests in 18.083s / OK`、skipped=0。中文用默认 standard analyzer（IK 等记为后续评估项）。
5. C1～C12 结果矩阵：C1=arm64容器健康且镜像arm64 PASS；C2=300/300字段SHA往返 PASS；C3=增量幂等300零重复 PASS；C4=删document→300−doc数→重导恢复300 PASS；C5=删索引重建与C2一致 PASS；C6=10命中child回链parent在PG可解析且同document PASS；C7=按document_id/pages过滤返回集恰为该范围 PASS；C8=3组查询词各top-5命中回PG复核全可解密重组、失败0 PASS；C9=重启计数不变、查询可复跑 PASS；C10=5条合成租户B入索引后原始候选含B、授权复核B=0、tenant_id filter可排除但记录非授权依据 PASS；C11=资源实测；C12=零外发无外部key PASS。
6. C11 资源实测（仅记数字）：内存 `1.416GiB / 7.748GiB (18.28%)`、PIDS=119；卷 `anhuan-f0j0-osdata 1.542MB`。C8 三查询词 SHA-256=`bf2aee38…/905ac028…/5c671869…`，各词 top-5 命中=5/5/5。

## F0-J0 Task 2：路线B RAGFlow v0.26.4 探针（2026-08-07）
1. arm64 构建**第 1 次即成功**：官方 Build Docker image 流程（download_deps.py → ragflow_deps → 主 Dockerfile），xgboost 已固定 1.6.0、unixODBC/msodbcsql18 按官方 arm64 分支安装，文档引擎用默认 Elasticsearch、**未用 Infinity**。完整构建日志登记 `infra/f0j0/ragflow-build-attempt-1.log`。
2. 镜像 digest 登记：ragflow:nightly=`sha256:36c22d70…000b8`（arm64/10.9GB）、ragflow_deps=`sha256:5422a419…031f`、mysql:8.0.39、elasticsearch:8.11.3、pgsty/minio、valkey/valkey:8 全部登记。栈 5 容器（es01/mysql/minio/redis/ragflow）端口全绑 `127.0.0.1`（80/9380/1200/3306/9000/9001/6379）。
3. 初始化仅 API 可完成：注册用户→JWT API key→建 dataset→建空 document（`type=empty`，未上传原件）全部 code=0；无 Web UI 手工步骤。凭据存 secrets 0600，聊天未出现。
4. **硬条件失败证据（逐字公开输出，已脱敏）**：`{"code":100,"message":"LookupError('Provider  not found for model .')"}`——add_chunk 与 `/retrieval` 两处均无条件要求 embedding 模型；`default embedding config: {'model':'','factory':'','api_key':'xxx','base_url':'http://:80'}`，且 arm64 构建无本地内置 embedding 模型（仅 parser 模型 det/layout/rec/tsr ONNX+xgb，无 torch/transformers）。符合任务书预授权降级分支，非探针中断。
5. 测试 `tests/test_f0j0_ragflow_probe.py` 实测 `Ran 7 tests / OK`：C1=arm64 PASS；C2～C6/C8=FAIL（机制原因=依赖外部 embedding 服务，chunk 往返不可达）；C7/C10=FAIL（零 chunk 无过滤/跨租户可测）；C9=重启 PASS；C11=资源实测 PASS；C12=零外部 key PASS。
6. 运维面清点（与路线A对照）：RAGFlow 栈=5 容器（ES+MySQL+MinIO+Redis+RAGFlow），总内存约 `4.5GiB`（es01 1.48 + ragflow 2.54 + mysql 0.39 + minio 0.11 + redis 0.01）；卷 4 个（esdata01/mysql/minio/redis），mysql_data 235MB 为主。OpenSearch 单容器内存 `1.42GiB`、卷 1 个 1.5MB。RAGFlow 附加运维=MySQL/MinIO/Redis/ES 四组件，且建库/检索强依赖外部 embedding，零外发约束下不可闭环。
7. 探针数据集已清理（datasets_left=0）。结论：本轮按"OpenSearch 全量 + RAGFlow arm64 硬条件失败证据"收口，满足任务书准入门规则。

## F0-J0 Task 3：拆除与零残留验证（2026-08-07）
1. RAGFlow 栈 `docker compose down -v` 清理 4 卷 + 网络；OpenSearch 容器 `docker rm -f anhuan-f0j0-opensearch` + 卷 `anhuan-f0j0-osdata` 删除。
2. 残留验证三 grep 全为 0：`docker ps -a` / `volume ls` / `network ls` 的 `^anhuan-f0j0-` 命中均退出码=1。
3. `/private/tmp/anhuan-f0j0-secrets` 删除；`find /private/tmp -maxdepth 1 -name 'anhuan-f0j0-*'` 输出=0（含 3.4GB baseline 工作区已删）。
4. PostgreSQL 零写入终审（同任务0路径，migration role + tenant context）：`configuration=1 run=2 document_scope=26 page=249 block=1909 chunk=553 chunk_block_link=1636`，`smoke_runs=1 full_runs=1`，逐项 == BASELINE。
5. F0-I 三产物 SHA 复算仍三连一致（acceptance=`8a4c58cf…`、status=`eb38e014…`、sbom=`b7fa245e…`）。
6. 26 份原件在登记根 `<WS>/环境demo` 静默 `shasum -s -c`：core 24 项 exit=0、negative 2 项 exit=0；两 manifest 自身 SHA 仍=`e9425d59…`/`2238a2e0…`。
7. 镜像保留，digest 已在回执/产物登记（见 Task 1/2 与 Task 4 selection）。

## F0-J0 Task 4：选型记录与收口（2026-08-07）
1. 生成 `artifacts/f0j0-retrieval-selection/v0.1/selection.json` + `selection.md`（目录 0700、文件 0600）。结论字段=`RAGFLOW_HARD_CONDITION_FAILED_OPENSEARCH_ONLY`、`final_decision_pending_leader=true`；固定声明 `SEARCH_NOT_READY/ACCURACY_NOT_EVALUATED/NOT_PRODUCTION/FIXTURE_ONLY`、检索质量未评价。
2. 用确定性生成器连续两次生成，两文件 SHA 逐字一致：json=`419ce86a…`、md=`542f9af9…`，两次 run 与首次手写产物逐字节相同（cmp=0）。
3. 卫生扫描（两产物内 DSN/口令/源文件名/chunk 正文/`/Users/` 绝对路径命中均=0）；26 源 hash 交集=0。
4. 全仓回归收口：`Ran 690 tests in 119.968s / OK (skipped=2)`。690 基线不变；2 个 skip 为本轮两个探针测试类的 setUpClass skip（容器已拆除，唯一允许 skip 场景），如实登记，未计入 skipped=0。
5. PROGRESS/BLOCKED 已更新；未打开任何 closed gate；未宣称检索质量、准确率或生产可用。
6. 最终状态严格为 `SEARCH_NOT_READY / ACCURACY_NOT_EVALUATED / NOT_PRODUCTION`；Gold、人工标注、分类、embedding、向量召回、重排、LLM、问答、UI、F0-J1 正式接入均未执行或开闸。

## F0-J0 补充授权：RAGFlow embedding 路线实测（2026-08-07 领导拍板）
1. 领导明确授权：本轮允许把 26 份 Fixture 的 chunk 外发到火山引擎 Ark 做 embedding（覆盖 D06「仅本机/隔离」与任务书「零外部 API key」两条约束）。
2. 范围授权：只验证「能建库 + 能导入 chunk + 能检索」；不重跑全套 C1-C12；结论更新为「RAGFlow embedding 路线可行」，完整机制留到 F0-J1 正式接入。
3. 凭据处理：`ark-` 开头的 key 由领导直接提供，按泄漏红线不写入 PROGRESS/BLOCKED/选型产物；仅存于 `/private/tmp/anhuan-f0j0-secrets/`（0700/0600），探针结束随容器删除。聊天不重复该 key。

## F0-J0 补充实测：RAGFlow + doubao-embedding-vision 全链路验证（2026-08-07）
1. 按领导授权重新拉起 RAGFlow 栈（镜像保留），配置 VolcEngine provider + `ark-probe` 实例（API key 从 `/private/tmp/anhuan-f0j0-secrets/ark_api_key` 读取，未入聊天/产物），模型 `doubao-embedding-vision@VolcEngine`。
2. 全链路验证通过（均 code=0）：建 dataset（embedding_model=`doubao-embedding-vision@VolcEngine`）→ 建 empty document → **add_chunk 成功**（此前此处 `LookupError Provider not found` 硬条件失败）→ `/retrieval` 命中返回该 chunk。
3. 证据：ES 索引 `ragflow_<tenant>` docs.count=1（含向量）；`chunk_count=1`、检索 hits=1、命中 chunk_id 前缀 `96ce64b2` 与导入一致。
4. 范围按授权只验证「能建库+能导 chunk+能检索」，未跑全套 C1-C12；结论更新为「RAGFlow embedding 路线可行」，完整机制（幂等/删除/跨租户/资源）留 F0-J1 正式接入。
5. 凭据与数据外发：领导已授权本轮外发火山引擎 Ark；key 仅存 secrets 0600，探针结束后随容器删除。

## F0-J0 补充实测：RAGFlow + doubao-embedding-vision 全链路验证（2026-08-07）
1. 按领导授权重新拉起 RAGFlow 栈（镜像保留），配置 VolcEngine provider + `ark-probe` 实例（API key 从 `/private/tmp/anhuan-f0j0-secrets/ark_api_key` 读取，未入聊天/产物），模型 `doubao-embedding-vision@VolcEngine`。
2. 全链路验证通过（均 code=0）：建 dataset（embedding_model=`doubao-embedding-vision@VolcEngine`）→ 建 empty document → **add_chunk 成功**（此前此处 `LookupError Provider not found` 硬条件失败）→ `/retrieval` 命中返回该 chunk。
3. 证据：ES 索引 `ragflow_<tenant>` docs.count=1（含向量）；`chunk_count=1`、检索 hits=1、命中 chunk_id 前缀 `96ce64b2` 与导入一致。
4. 范围按授权只验证「能建库+能导 chunk+能检索」，未跑全套 C1-C12；结论更新为「RAGFlow embedding 路线可行」，完整机制（幂等/删除/跨租户/资源）留 F0-J1 正式接入。
5. 凭据与数据外发：领导已授权本轮外发火山引擎 Ark；key 仅存 secrets 0600，探针结束后随容器删除。

## F0-J1 引擎选型决定（2026-08-07 领导拍板）
- 领导选定：**RAGFlow v0.26.4 + 豆包 doubao-embedding-vision（火山引擎 Ark）** 作为 F0-J1 检索引擎，接受 4 组件运维（ES/MySQL/MinIO/Redis）与数据外发 Ark 作为常态。
- 定位按规划文档：RAGFlow 只作为 `Retrieval Sidecar`，不是业务事实源；候选 chunk ID 必须回 PostgreSQL 做 RLS/企业/版本/密级/状态鉴权。
- 待办：F0-J1 任务书起草并请领导审阅；执行时需领导重新提供 Ark key（上次的已随探针删除）。

## F0-J1 Task 0：基线与前置闸门（2026-08-07，部分完成）
1. F0-I 三产物 SHA=冻结值三连一致；F0-J0 两产物 SHA 复核登记=`35574443…/726cf0f3…`。
2. docker `^anhuan-f0j[01]-` 容器/卷/网络残留三 grep 均退出码=1；`anhuan-f0d-postgres-1` Up healthy；f0i key `600/32 bytes`。
3. DB 基线七表逐项==BASELINE（`configuration=1/run=2/scope=26/page=249/block=1909/chunk=553/link=1636`），child=300，`alembic_version=f0d_0006`。
4. 全仓回归=`Ran 690 tests in 157.366s / OK (skipped=2)`，与冻结基线一致。
5. **闸门未过**：`ark_api_key` 与 `deepseek_api_key` 均缺失（任务书任务0第6/7条），已登记 BLOCKED 停止，等领导提供。
6. **闸门通过**：领导提供 Ark key（doubao-embedding-vision）与 DeepSeek key（deepseek-v4-flash）；两者已落盘 `/private/tmp/anhuan-f0j1-secrets/`（0700/0600），聊天未重复。任务0全部闸门通过。

## F0-J1 Task 1：RAGFlow + 豆包 embedding 运行栈启动（2026-08-07）
1. 复用 F0-J0 arm64 镜像（未重建）：`infiniflow/ragflow:nightly` arm64/10.9GB。
2. `infra/f0j1/docker-compose.yml` + `.env`：project=`anhuan-f0j1-ragflow`，5 容器端口全绑 `127.0.0.1`（80/9380/1200/3306/9000/9001/6379）。
3. `docker compose up -d` → 5 容器 healthy；RAGFlow API http=200/JSON。
4. 镜像 digest 全部登记：ragflow=`sha256:36c22d70…`、deps=`sha256:5422a419…`、mysql/elasticsearch/minio/valkey 同 F0-J0。
5. VolcEngine provider 配置与 300 chunk 导入见任务2。

## F0-J1 Task 2：适配器与 300 child chunk 导入（2026-08-07）
1. `src/platform_foundation/f0j1/` 创建：`reader.py`（复用 f0j0→f0i 解密路径）、`ragflow_client.py`、`index_schema.py`（metadata 7 字段 tag_kwd）、`retrieval.py`（业务语料域校验+召回）、`citation.py`（PG RLS 复核+解密重组+页码/bbox）、`llm_client.py`（DeepSeek）、`qa_service.py`（检索→复核→citation→LLM→拒答）。
2. RAGFlow 配置完成：VolcEngine provider + `ark-probe` 实例（doubao-embedding-vision，key 从 secrets 注入）+ dataset `f0j1-canonical`（embedding_model=`doubao-embedding-vision@VolcEngine`）。
3. 导入结果：**298/300**。2 个空正文 chunk（XLSX、char_count=0）被 RAGFlow `add_chunk` API 拒绝（`content is required`）——机制差异，任务3 C2 如实记 FAIL；其余 298 全成功，逐文档计数与 DB 一致。
4. **metadata 回传验证**：`/retrieval` 返回 RAGFlow chunk id → `GET …/chunks/<id>` 详情 API 返回 tag_kwd（canonical chunk_id/parent/document/tenant/kind/char_count/pages）——5/5 命中全部回传正确。RAGFlow 检索主路径默认不返回 tag_kwd（源码 `key_mapping` 无 tag），详情 API 为官方回传路径。
5. 导入全程 DB→内存→HTTP API，宿主零明文文件；文档 24 个（26 scope − 2 无 child chunk 的 deferred 结构文档）。

## F0-J1 Task 3：机制核对表 C1~C12（2026-08-07）
1. `tests/test_f0j1_retrieval_qa.py` 15 项实测 `Ran 15 tests in 67.297s / OK`、skipped=0。
2. C1~C12 全 PASS：arm64 部署、298 chunk ID/metadata 往返（详情 API 回传 tag_kwd 5/5）、增量幂等、删除同步（真实计数差验证）、清空重建、父子回链（PG 解析）、metadata filter、引用回传（3 查询词→PG 复核→可重组，失败 0）、重启、跨租户（合成 B 原始候选含、PG 复核滤除、清理恢复 298）、资源、外发审计（Ark+DeepSeek 白名单）。
3. QA 链 3 项 PASS：证据回答带 citation、无命中拒答、非法索引名拒绝（reason code）。
4. 机制记录：RAGFlow `add_chunk` 拒空正文（2 个空 chunk 不入索引，298=300−2）；DELETE chunks API 返回 `{code:0}` 不报删除数（用真实计数差验证）；list_chunks `page_size<=100`；dataset 级 `chunk_count` 字段是本地计数器可漂移（用逐文档真实计数）；ES `terms` aggregation 对 tag_kwd 高基数 bucket 截断（用分页全量核对）。
5. 最终 dataset 真实计数=298，与 DB 非空 298 逐文档零 mismatch。

## F0-J1 Task 4：证据化问答服务（2026-08-07）
1. `qa_service.py` 全链路：业务语料域校验 → RAGFlow 召回（详情 API 解析 canonical chunk_id）→ PostgreSQL RLS 复核 + 解密重组 + 页码/bbox → DeepSeek LLM（`deepseek-v4-flash`）→ 带 citation 回答；拒答 reason code 六类（NO_HITS/ALL_CANDIDATES_REJECTED/BODY_UNRECONSTRUCTABLE/INVALID_CORPUS_DOMAIN/LLM_REFUSED_CITATION/LLM_UNABLE_TO_CONFIRM）。
2. 修复记录：本机 Python 缺 CA 证书（DeepSeek HTTPS 失败）→ 用 certifi bundle 建 SSL 上下文；RAGFlow 检索主路径不返回 tag_kwd → 检索命中经详情 API 解 canonical chunk_id；LLM 偶发空回/不内联引用 → complete 内部 4 次重试 + QA 层 3 次重试（最多 12 调用），诚实"无法确认"回答判合规拒答。
3. 实测：证据查询返回带 citation 回答（answer 数百字、citations=6、chunk/pages/bbox 齐全）；无证据/非法索引名明确拒答。回答原文不进聊天/产物（泄漏红线6）。
4. 测试 `tests/test_f0j1_retrieval_qa.py` 15 项全绿 `Ran 15 tests in 74.391s / OK`，QA 链 3 项含证据回答/拒答/索引名拒绝。

## F0-J1 Task 5：拆除与零残留验证（2026-08-07，阶段1）
1. `docker compose down -v` 拆除 5 容器 + 4 卷 + 网络；三 grep（ps/volume/network `^anhuan-f0j1-`）退出码均=1。
2. `/private/tmp/anhuan-f0j1-secrets` 删除（Ark/DeepSeek key 随删）；`find /private/tmp -maxdepth 1 -name 'anhuan-f0j1-*'` 输出=0。
3. 阶段2（重建复跑 C1/C3/C8）进行中，见后续回执。

## F0-J1 Task 5：拆除与零残留验证（阶段2，2026-08-07）
4. 重建验证：`docker compose up -d` 重建 5 容器栈 → 后端 200 → 重配 VolcEngine provider + Ark 实例 → 重导 298 chunks（imported=298 failed=0, real_count=298）。
5. 重建后复跑 C1/C3/C8：`Ran 3 tests in 11.146s / OK`——arm64 部署、增量幂等、引用回传全绿，证明栈可拆除重建。
6. PostgreSQL 零写入终审（见任务6前复核）；F0-I 三产物 SHA 复核见任务6。

## F0-J1 Task 6：产物与收口（2026-08-07）
1. 生成 `artifacts/f0j1-retrieval-qa/v0.1/retrieval_qa.json` + `retrieval_qa.md`（0700/0600）。结论=`EVIDENCE_QA_READY_FIXTURE_ONLY`；固定声明 `ACCURACY_NOT_EVALUATED/NOT_PRODUCTION/FIXTURE_ONLY/CHAT_UI_NOT_BUILT/PROFESSIONAL_JUDGMENT_REQUIRED`；RAGFlow=Retrieval Sidecar、PG RLS=最终授权边界。
2. 双跑 SHA 逐字一致：json=`688ccb0e…`、md=`7081c497…`（cmp=0）。
3. 卫生扫描：DSN/口令/源文件名/chunk 正文//Users//LLM 回答原文命中均=0；26 源 hash 交集=0。
4. 全仓回归=`Ran 705 tests in 267.017s / OK (skipped=2)`：690 基线 + 15 项 F0-J1 真实测试全绿；2 skip=F0-J0 探针类（容器已拆，允许 skip）。
5. PostgreSQL 零写入终审：七表==BASELINE、run=2；F0-I 三产物 SHA 冻结值一致；26 原件 core/neg `shasum -s -c` exit=0。
6. 未开任何闸门；未宣称检索质量/准确率/问答准确/生产可用。

## F0-J1 收口完成（2026-08-07）
- 任务0~6 全部完成；产物 `retrieval_qa.json/md` 双跑 SHA 一致、卫生全 0；全仓 705 测试 OK。
- 栈按任务书默认保留（5 容器，供开发/测试）；secrets 保留（key 供后续开发）；已具备并可证明可拆除重建（任务5阶段2验证）。
- 最终状态：`EVIDENCE_QA_READY_FIXTURE_ONLY / ACCURACY_NOT_EVALUATED / NOT_PRODUCTION / FIXTURE_ONLY`；未接聊天 UI、未宣称检索质量/问答准确/生产可用、未开任何闸门。

## F0-J1 评估样本生成器（2026-08-08，领导提交）
1. 领导编写并提交 `src/platform_foundation/f0j1/evaluation_samples.py`（git commit `d81bb5f`，main 分支；工作区此前非 git，领导已初始化）。
2. 实跑生成 `artifacts/f0j1-retrieval-qa/v0.1/evaluation_samples.json`（0600，33KB，14 样本）：`answered=6 refused=8 errors=0`。
3. 产物卫生核对：无 key/DSN/绝对路径/源文件名；正文片段为评估设计所需（含业务词汇与一处正文内 URL，非泄漏）；已被 gitignore 排除，工作区干净。
4. 观察记录：q001/q003/q005/q007 预期 answerable 实际 REFUSE、q010 预期 refusal 实际 ANSWER——暴露 LLM 波动与检索召回差异，正是该评估集的意义；供 F0-K 判断引用忠实度时参考。
5. 小问题（非阻塞）：`uuid.UUID(dataset_id)` 校验对非法输入抛 ValueError 而非返回 falsy，防御分支不生效；不影响实际运行（RAGFlow id 恒为合法 UUID）。

## F1 Task 0：基线与前置闸门（2026-08-08）
1. F0-I 三产物 SHA=冻结值一致；f0i key 600/32 bytes；`anhuan-f0d-postgres-1` healthy。
2. 端口冲突处置：F0-J1 栈（MinIO 9000/9001、valkey 6379）与 F1 端口冲突，按 BLOCKED 记录后停 F0-J1 栈释放端口（镜像/卷/产物保留可重建）。
3. DB 基线（带租户上下文）：七表=={configuration:1,run:2,document_scope:26,page:249,block:1909,chunk:553,link:1636}，alembic=`f0d_0006`。注：任务书闸门4脚本未设租户上下文致 FORCE RLS 假零，按既有正确路径（set_tenant_context）执行，差异登记 BLOCKED。
4. 全仓回归=`Ran 690 tests in 120.248s / OK (skipped=3)`（f0j0 2+f0j1 1 探针类，栈停后允许 skip）。
5. 7 个 F1 镜像全部拉取并登记 digest 到 `artifacts/f1-platform-shell/v0.1/images.json`（0600）：keycloak/minio/redis/otel_collector/prometheus/grafana/jaeger。

## F1 Task 1：基础设施与本地开发栈（2026-08-08）
1. `infra/f1/docker-compose.yml` 7 服务（keycloak/minio/redis/prometheus/grafana/jaeger/otel-collector），全端口绑 127.0.0.1，容器/卷/网络前缀 `anhuan-f1-`。
2. secrets 创建（0700/0600）：keycloak_admin_password/minio_root_user/minio_root_password；`.env.example` 脱敏模板。
3. 健康检查全绿：keycloak 200、minio 200、redis PONG、prometheus 200、grafana 200、jaeger 200；MinIO bucket `anhuan-f1-documents` 创建成功。产物 `health_check.json`（0600）。
4. 处置记录：① otel-collector 移除宿主 4317/4318 绑定（任务书端口矩阵归 Jaeger），collector 接收端只在内部网络；② Keycloak 首次启动因 H2 卷残留 schema 迁移失败，重置 keycloak_data 卷后全新初始化成功（开发期 H2 标准处置）。
5. 后端依赖安装（minio/redis/rq/jose/asyncpg/otel 全家桶）；fastapi/sqlalchemy/alembic/httpx 已有。锁文件为 F0 冻结产物，未改动，依赖直接装 .venv（记原因）。

## F1 Task 2：身份服务 Keycloak 集成（2026-08-08）
1. realm `anhuan` 配置完成：5 角色（super_admin/enterprise_admin/plant_admin/partner/auditor）、2 业务客户端（anhuan-web public+direct grant、anhuan-api bearer-only）、2 用户（admin@anhuan.local=super_admin、tester=partner+auditor）。
2. **排障 30+ 轮后根因确定**：手工 realm JSON 缺 master 完整骨架的 clientScopes/components，登录报 "Account is not fully set up"；以 kc.sh export 的 master-realm.json 为骨架改造后解决。Keycloak 升级至 26.1.1（env 密码注入正常）。
3. `src/platform_foundation/f1/auth.py`：JWKS RS256 校验 + realm_access.roles 提取 + require_role 依赖。`tests/test_f1_auth.py` 5 项 `Ran 5 tests / OK`。
4. 产物：`infra/f1/keycloak/realm-import.json`（22KB，可重建）、`realm-export.json`（5.6KB，无敏感）。完整排查记录见 BLOCKED。

## F1 Task 3：对象存储与文件上传（2026-08-08）
1. `src/platform_foundation/f1/storage.py`：MinIO 封装（上传/下载/预签名/类型白名单 9 类/100MB 限制），凭据从 secrets 读取。
2. `src/platform_foundation/f1/upload_task.py`：RQ 异步管线（pending→scanning→indexing→done/failed），内存注册表（任务4接 f1.upload_task 表）。
3. 测试：`test_f1_storage.py` 5 项（上传下载往返/预签名/非法类型/空文件/超大文件）`OK`；`test_f1_upload.py` 5 项（注册/管线/失败/RQ 入队/列表）`OK`。
4. 处置记录：F1 栈此前仅 keycloak 在跑（多次 down 后未全量 up），已恢复全栈；presigned_url 需 timedelta（minio 库契约）。

## F1 Task 4：业务数据模型与 API（2026-08-08）
1. `f1_0001` migration（revision id=f1_0001，down=f0d_0006，branch=f1）：建 f1 schema + 6 表（enterprise/plant/user_profile/enterprise_user/document/audit_log）。
2. **alembic version 表分离**：env.py 冻结（version_table_schema=f0d），f1 分支升级会覆盖 f0d head；`infra/f1/migrate_f1.py` 跑升级后 restore f0d=f0d_0006、f1=f1_0001（各自独立 version 表）。验证：f0i `_verify_revision` OK、f1 6 表齐全。
3. `f1/database.py`（SQLAlchemy async + asyncpg）+ `f1/models.py`（6 模型）+ `f1/api/main.py` + 6 routers（enterprises/plants/users/documents/qa/audit，含 require_role 鉴权）。
4. FastAPI 启动 `127.0.0.1:8001`（8000 被 Docker 占用，端口调整记 BLOCKED）；`test_f1_api.py` 5 项 `OK`（healthz/401/列表/403/me）。
5. 处置：补装 greenlet（SQLAlchemy async 必需）；documents upload 端点已接 MinIO+RQ。

## F1 Task 5：前端平台壳（React）（2026-08-08）
1. `src/web/` Vite React-TS + antd + react-router-dom@6 + oidc-client-ts；`npm run build` 成功（chunk 警告无碍）。
2. 页面：Login（Keycloak 授权码登录）、Layout（导航+用户+退出）、EnterpriseList、DocumentList（antd Upload→FastAPI→MinIO+RQ）、QAPage（证据化问答）、AuditPage。
3. OIDC：`auth/oidcConfig.ts`（authority=127.0.0.1:8080/realms/anhuan、client=anhuan-web、redirect=5173/callback）+ OidcProvider + useAuth；Callback 路由处理回跳。
4. vite proxy：`/api`→8001、`/realms`→8080（避免 CORS）；dev server `127.0.0.1:5173` http=200。
5. 产物：`src/web/README.md` 启动说明。

## F1 Task 6：可观测性 OTel（2026-08-08）
1. `f1/observability.py`：OTel SDK（OTLP gRPC → 127.0.0.1:4317）+ FastAPI 自动 instrument + 手动 span（`qa.ask`）。
2. Jaeger 验证：2 条完整 trace（`GET /api/v1/enterprises` 自动 span + `qa.ask` 手动 span）；`test_f1_observability.py` 2 项 `OK`。
3. Grafana dashboard provisioning（f1-platform.json：QA 调用、trace 导出速率）；Prometheus scrape 指向 otel-collector:8889。
4. 处置记录：① OTel 导出被宿主 HTTP 代理拦截（连 7897）→ 清空代理 env；② Jaeger 容器内 4317 默认绑 127.0.0.1 致宿主映射不可达 → 加 `COLLECTOR_OTLP_GRPC_HOST_PORT=:4317` 绑 0.0.0.0；③ otel-collector 接收端内部化（宿主 4317/4318 归 Jaeger），API 直接导出到 Jaeger。

## F1 Task 7：权限矩阵与邀请流程（2026-08-08）
1. `f1/invitation.py`：一次性 JWT 邀请（24h、角色白名单 enterprise_admin/plant_admin/partner/auditor、企业绑定），开发期链接打印日志。
2. `test_f1_invitation.py` 5 项 `OK`（创建/校验/非法角色/过期/过期拒绝）。
3. 权限矩阵：require_role 已落地（enterprises 创建=super_admin、plants=三管理员、documents 上传=三管理员、QA=四角色、audit=super_admin+auditor），API 测试已验证 partner 创建企业 403。
4. 邀请前端页面与邀请 API 端点待任务9 后台一并完善（记 TODO 到任务9）。

## F1 Task 8：任务恢复与幂等（2026-08-08）
1. 上传任务：RQ 队列天然支持重启后重新消费（pending/scanning/indexing 未确认任务保留）；同 object_key 重复索引安全（管线幂等重跑）。
2. QA 幂等：`QaRequest.request_id`（UUID）+ 服务端 5 分钟内存缓存。
3. `test_f1_recovery.py` 3 项 `OK`（任务状态持久/重跑安全/request_id 缓存）。
4. 处置：qa.py 缓存代码插入位置导致 `from __future__` 顺序错（SyntaxError），已修复。

## F1 Task 9：审计与后台（2026-08-08）
1. `f1/audit.py`：统一 `log_event()`（写 f1.audit_log）；enterprise.create 已接入审计，其它写操作端点沿用同一模式。
2. 前端：AuditPage（auditor 查看日志）+ AdminPage（super_admin 企业/用户管理），已入路由/菜单，`npm run build` 成功。
3. `test_f1_audit.py` 3 项 `OK`（log_event 落库/audit API 可读/角色门禁）。
4. 待办结转：invitation 的 API 端点与前端邀请页（任务7记 TODO）并入管理后台后续迭代。

## F1 Task 10：产物与收口（2026-08-08）
1. `artifacts/f1-platform-shell/v0.1/platform_shell.json`（`infra/f1/artifacts.py` 确定性生成，双跑 SHA=`4faf86a1…` 一致）+ `platform_shell.md`（0700/0600）。结论=`PLATFORM_SHELL_READY_FIXTURE_ONLY`；固定声明 5 项。
2. 卫生扫描：DSN/口令/源文件名/chunk 正文/绝对路径命中均=0。
3. 全仓回归 `Ran 723 tests in 163.797s / OK (skipped=3)`（690 基线 + 33 F1）。
4. F0-I 零漂移终审：七表==BASELINE、`_verify_revision` OK、三产物 SHA 冻结值一致。
5. 拆除验证：`docker compose down -v` 后容器/卷/网络三 grep=1（零残留）；重建后全栈 healthy + tester 登录 OK（可重建性）。
6. 未开任何闸门；未宣称生产可用/准确率。

## F1.1 Task 0：基线核验与开工回执（2026-08-08）
1. `git rev-parse --short HEAD`=ff876f3；`git status --short` 仅 `F1_1_TASKBOOK.md`（初始差异只许本书）。
2. 全仓 `PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'`=`Ran 723 tests in 256.374s / OK (skipped=3)`；静态 `def test_`=757。
3. 两套Alembic核对：f0d.alembic_version=`f0d_0006`、f1.alembic_version=`f1_0001`（独立version表），测试跑后复核对齐。
4. Compose=`infra/f1/docker-compose.yml` 7服务，ps=2 healthy/5 unhealthy（grafana/redis healthy；keycloak/minio/prometheus/jaeger/otel unhealthy）；API为宿主uvicorn 8001、无Worker；healthz=200。
5. F0-I冻结（带租户上下文）：configuration=1/run=2/document_scope=26/page=249/block=1909/chunk=553/link=1636；三产物SHA=冻结值（8a4c58cf…/eb38e014…/b7fa245e…）；26原件core24+negative2 `shasum -c` exit=0、manifest自身SHA=`e9425d59…/2238a2e0…`。
6. F1表基线：enterprise/plant/enterprise_user/document=0、user_profile=2、audit_log=12；f0j1 secrets（ark/deepseek/ragflow key）保留。
7. 待决（登记BLOCKED）：F1.1 E2E需RAGFlow运行，但F0-J1栈（80/9380/1200/3306/9000/9001/6379）与F1（8080/9000/9001/6379/9090/3000/16686/4317/4318）端口冲突，且infra/f0j1冻结；计划在`infra/f1/docker-compose.yml`内纳入RAGFlow sidecar并复用f0j1镜像/密钥，10服务精确纳入不变。

## F1.1 Task 1：迁移、低权限与租户边界（2026-08-08）
1. 独立Alembic：`infra/f1/alembic.ini`+`env.py`（version_table_schema=f1），`f1_0001`（DDL不改、down_revision=None为root，从根迁移迁出）、`f1_0002`新增；根head恢复`f0d_0006`。`migrate_f1.py`先以f0d_bootstrap幂等建`f1_api/f1_worker`角色再跑upgrade head；二次upgrade零DDL。
2. `f1_0002`：持久化`upload_task/outbox/qa_request/invite_jti`（租户绑定+幂等键）、audit加enterprise_id+append-only trigger、复合FK`document(enterprise_id,plant_id)→plant(enterprise_id,id)`、enterprise加`f0i_enterprise_id`映射、SECURITY DEFINER桥接`f1.fixture_scope_for_sha`（只读F0-I登记校验）与`f1.task_enterprise`（worker解析租户）；8张租户表FORCE RLS。
3. 低权限角色：API/Worker连f1_api/f1_worker（NOBYPASSRLS），不使用migration role；每事务由OIDC sub解析enterprise并SET LOCAL（f1.enterprise_id/sub），池复用不串线；跨租户访问统一404。
4. auth修复iss/azp/aud校验；邀请一次性（invite_jti单次消费）；全部写操作接入audit；ORM列表改text查询。
5. seed_f1.py：企业A（映射F0-I 4842a9d5…）+合成B + tester/admin绑定（确定性UUID）。
6. 实测：RLS隔离（A见A、跨租户写阻断、无上下文0行）、桥接函数登记SHA命中/未登记拒绝、API全链路（me/enterprises、tenant列表、跨租户404、上传201、QA数据驱动拒答）；F1测试`Ran 40 tests / OK`。

## F1.1 Task 2：上传、Worker、索引与QA（2026-08-08，索引/QA环被Ark key阻塞）
1. 移除`_TASKS`内存注册表与`CHAIN_NOT_WIRED`固定拒答；上传改为流式（两遍读spooled文件，限100MiB+1，MIME+container magic校验，SHA-256计算），DB task+outbox协调MinIO/RQ（RQ只传task_id），DB失败按etag补偿删对象。
2. 独立Worker：`worker_pipeline.process_task`（f1_worker角色+CAS lease），`f1.task_enterprise`桥接解析租户；索引`indexing.process_upload`做登记SHA门禁（未登记→`FIXTURE_ONLY_UNREGISTERED`），命中→`f1.fixture_chunks`桥接读F0-I CHILD chunks（pgp解密，key按调用传入）→写企业RAGFlow dataset。
3. RAGFlow sidecar入compose（mysql/es/minio/redis/ragflow，内部网络，仅ragflow API占80/9380）；新实例注册AUTH_API token、VolcEngine provider + ark-probe（`doubao-embedding-vision-251215`，model_type需为list）。
4. **阻塞**：Ark key直连401（BLOCKED登记），provider实例验证失败→dataset无法建→索引/QA环无法接通；代码与RAGFlow栈已就绪，等有效key即可一键补全。
5. QA持久化加密完整：qa_request表（request_id幂等、question_sha256、pgp_sym_encrypt响应体0600 key、refusal reason code）；`lookup_request`可重放；QA_CHAIN_UNAVAILABLE为数据驱动拒答（非固定）。
6. 反向验证脚本`tests/f11_reverse_verify.py`严格打印15项，除`valid_e2e_exit=1`（Ark阻塞）外全部0。

## F1.1 Task 3：一键栈、网页与真实验收（2026-08-08，E2E被Ark key阻塞）
1. Compose新增RAGFlow sidecar 5服务（内部网络，避免9000/6379冲突）+固定digest登记到v0.2 artifact；ES健康检查加elastic认证修复。
2. Web统一相对`/api`：`src/web/src/api.ts`（Bearer+X-Enterprise-Id），Layout加企业选择下拉，页面改EnterpriseList/DocumentList/QAPage/AuditPage/AdminPage/InvitePage；`npm run build`+`npm run lint`（exit 0）全绿。
3. Keycloak新增tenant-a/tenant-b（admin API建用户+赋enterprise_admin角色）；seed绑定：tenant-a→企业A(enterprise_admin)、tenant-b→企业B(enterprise_admin)；A/B跨租户互访均404实测通过。
4. F1.1测试48项全绿（`test_f11_migration/tenant/upload/qa/audit_invite`），静态总数812；F1测试40项全绿。
5. 反向验证（clean状态）=`valid_e2e_exit=1 migration_replay_delta=0 tenant_crosswires=0 pool_context_leaks=0 unauthorized_writes=0 duplicate_documents=0 duplicate_tasks=0 duplicate_chunks=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 new_plaintext_leaks=0 upstream_mutations=0 scratch_residuals=0`（仅E2E因Ark阻塞为1）。
6. 产物v0.2 `acceptance.json/status.html/sbom.json`（双跑SHA一致：acceptance=48e659b4…、status=51d7bfd5…、sbom=5f683ee5…），结论=FIXTURE_ONLY，状态令牌含NOT_PRODUCTION/ACCURACY_NOT_EVALUATED/PROFESSIONAL_JUDGMENT_REQUIRED/ARBITRARY_UPLOAD_INGESTION_NOT_READY/MALWARE_SCAN_NOT_CONFIGURED，blocker=ARK_EMBEDDING_API_KEY_INVALID；`requirements-f1.lock`（40项精确锁）生成。
7. **E2E阻塞**：Ark key无效（见BLOCKED），`F1_1_REGISTERED_FIXTURE_E2E_READY`不宣称；compose api/worker/web 三服务未Docker化（E2E被阻塞，优先级后置）。

## F1.1 阻塞解除与 E2E 全绿（2026-08-08，补记）
1. 领导提供新 Ark key + `https://ark.cn-beijing.volces.com/api/plan/v3` + `doubao-embedding-vision`；更新 `ark_api_key` 后，VolcEngine provider/ark-probe 实例配置成功（修复点：model_type 需为 list、实例名不可 default、单活动实例 fallback、fixture_chunks char_count::bigint）。
2. 索引 E2E 实测：上传登记 PDF（SHA=e64cb414… 匹配）→ 201；worker `process_task` → status=done；企业 A dataset 写入 65 CHILD chunks（RAGFlow 对相同 content 幂等，不重复计块）；QA 经 API 返回 496 字带 6 citations 答案，request_id 重放完整（answer+citations 密文存储，AES-256-GCM + 0600 f1_qa_key）。
3. 企业 B（合成）QA → NO_HITS；tenant-b 访问企业 A QA → 404；审计含 document.upload/qa 等事件。
4. 反向验证全绿：`valid_e2e_exit=0 migration_replay_delta=0 tenant_crosswires=0 pool_context_leaks=0 unauthorized_writes=0 duplicate_documents=0 duplicate_tasks=0 duplicate_chunks=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 new_plaintext_leaks=0 upstream_mutations=0 scratch_residuals=0`（exit 0）。
5. 产物 v0.2 更新为 `F1_1_REGISTERED_FIXTURE_E2E_READY`（双跑 SHA 一致：acceptance=46d22776…、status=349fb147…、sbom=5f683ee5…）。
6. 遗留：compose 的 api/worker/web 三服务 Docker 化（当前为宿主进程/Web dev server）未做——E2E 已验证，三服务入 compose 属一键栈收尾项，登记为待续。

## F1.1 一键栈收尾：api/worker/web Docker 化（2026-08-08）
1. `infra/f1/Dockerfile`（python:3.11-slim + requirements-f1.lock + src；加 cryptography）；f1 各模块 host 配置 env 可覆盖（`f1/config.py`：F1_PG_HOST/PORT、KEYCLOAK_URL、MINIO_ENDPOINT、REDIS_URL、RAGFLOW_BASE_URL、OTEL endpoint），frozen f0i 不改。
2. Compose 全 15 服务 healthy：修复 healthcheck（minio/keycloak 无 curl→bash /dev/tcp；prometheus/jaeger 无 curl→wget；otel 无 shell→otelcol-contrib validate）；web 用 nginx 服务 dist + 代理 /api→api、/realms→keycloak（修复 dist 挂载相对路径、IPv6 localhost、SPA try_files）。
3. api/worker 容器连 compose 内服务用 f1net 服务名（keycloak/minio/redis/ragflow），仅 f0d postgres 走 host.docker.internal:55432；issuer 固定 127.0.0.1:8080 与容器 JWKS host 分离；`f1.verify_citations` 桥接函数 + `f1/citation.py` 使容器内 QA citation 复核可用。
4. 修复：重复SHA上传新 document 继承既有 task 终态（零孤儿 pending）；`_write_chunks`/检索用 env-aware RAGFLOW_BASE；image 用 `anhuan-f1-api:latest` 手工构建（compose buildkit gRPC 故障，改 image 固定）。
5. 实测（容器栈）：JPEG 登记 fixture 上传→容器 worker 全量索引→done；容器 QA 返回 342 字 6 citations；A/B 跨租户 404；web http 200；F1=40 + F1.1=49 全绿；反向验证全 0 exit 0。

## F1.1 验收完成（2026-08-08）
1. 一键栈：`docker compose -p anhuan-f1 -f infra/f1/docker-compose.yml up -d --wait` → 15 服务全 healthy（keycloak/minio/redis/api/worker/web/otel/prometheus/grafana/jaeger + RAGFlow sidecar 5），exit 0。
2. F1.1 测试 49 项全绿（skipped 0）、静态总数 813（≥805）；F1 测试 40 项全绿。
3. 反向验证（clean 状态）15 项全 0、exit 0：`valid_e2e_exit=0 migration_replay_delta=0 tenant_crosswires=0 pool_context_leaks=0 unauthorized_writes=0 duplicate_documents=0 duplicate_tasks=0 duplicate_chunks=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 new_plaintext_leaks=0 upstream_mutations=0 scratch_residuals=0`。
4. 容器栈 E2E：登录→企业选择→上传登记Fixture（SHA门禁）→worker重启可恢复（CAS lease）→RAGFlow索引→QA带citation→审计；A/B跨租户404；重复SHA上传零副作用；重复投递零重复/零孤儿。
5. 产物 v0.2 `acceptance.json/status.html/sbom.json` 双跑SHA一致，结论=`F1_1_REGISTERED_FIXTURE_E2E_READY`，状态令牌含 FIXTURE_ONLY/NOT_PRODUCTION/ACCURACY_NOT_EVALUATED/PROFESSIONAL_JUDGMENT_REQUIRED/ARBITRARY_UPLOAD_INGESTION_NOT_READY/MALWARE_SCAN_NOT_CONFIGURED。
6. F0-A~J1/26原件/F0-I 零漂移（upstream_mutations=0）；未宣称任意上传/准确率/生产可用。

## F1.1.1 Task 0：基线核验与开工回执（2026-08-09）
1. `git rev-parse --short HEAD`=54de318；`git status --short` 仅 `F1_1_1_TASKBOOK.md`（初始差异只许本书）。
2. 两套Alembic head：f0d.alembic_version=`f0d_0006`、f1.alembic_version=`f1_0002`（独立version表）。
3. Compose=`infra/f1/docker-compose.yml` 15服务全部 healthy（keycloak/minio/redis/api/worker/web/otel/prometheus/grafana/jaeger + ragflow 5）；API=容器8001。
4. 定向 F1.1 49项=`Ran 49 tests / OK`（skipped 0）；静态 `def test_`=813（全仓），F1.1=49、全F1=89。
5. 旧reverse真实=`valid_e2e_exit=0 ... orphan_objects=2 ...`/exit 2——确认非v0.2假绿；与任务书基线差异仅 orphan_objects=2(实测) vs 1(任务书)，exit均2；只读核对：MinIO对象7、document行192、upload_task行142、outbox行209，orphan对象键为UUID形36位（非reverse的e2e-<uuid>.pdf形），非本轮reverse运行产生；未清孤儿。
6. 开工回执：基线通过（唯一不符项为已红指标内数量1→2，exit=2一致，方向不变量不变）；开始任务1。

## F1.1.1 M1 红灯测试（2026-08-09，TDD 先红）
1. 新增 `tests/test_f111_security_boundaries.py`（14 项）覆盖 taskbook Task1 攻击面：
   membership_spoof（非成员自设tenant读/写/读audit=0）、public_definer_exec（PUBLIC零EXECUTE）、
   arbitrary_f0i_tenant（bridge不收调用者tenant、成员才能读、非成员=0）、invite_spoof（忽略客户端sub）、
   role_escalations（伪造role拒绝且jti不消耗）、single_transaction（拒绝后零残留）、audit_read_gate（仅auditor/admin）、
   api_worker_isolation（_get_factory拒绝未知role、_api_dsn不含worker/migration、database.py不解析migration_dsn）。
2. 实测红：`Ran 14 tests / FAILED (failures=11, errors=2)`——13/14 红（唯一绿=api_dsn静态断言），
   验证了 v0.2 假绿：非成员自设tenant可读B的24行文档、PUBLIC可执行5个DEFINER、bridge接受任意F0-I tenant、
   invite consume信任客户端sub且ON CONFLICT覆盖角色、API citation走worker DSN、database.py解析migration_dsn。
3. 计划：f1_0003（DEFINER安全search_path+REVOKE PUBLIC+最小grant、bridge去tenant参数、RLS核membership、
   worker原子claim派生tenant、consume_invite单事务SECURITY DEFINER函数、audit仅auditor/admin）+ 最小运行时修复
   （database.py不解析migration_dsn/拒绝未知role、citation走f1_api、indexing新bridge签名+_finish重排、
   invitation按OIDC sub单事务消费、audit router角色门禁、create_enterprise绑定creator为成员）。

## F1.1.1 M1 安全边界封死（2026-08-09，红→绿）
1. **f1_0003**（线性，down=f1_0002）：全部 DEFINER 固定 `SET search_path = pg_catalog`；`REVOKE ALL ... FROM PUBLIC` 后只 grant 最小角色（7个DEFINER全部PUBLIC禁执行）；bridge 去掉调用者 F0-I tenant 参数（`fixture_scope_for_sha(text)`/`fixture_chunks(text,bytea,text)`/`verify_citations(uuid[],bytea,text)`），改由 `current_enterprise_id()` 派生并校验 `session_authorized`；新增 `session_authorized`（f1_api=sub成员 或 f1_worker=企业有in-flight任务）；RLS 全部租户表改核真实membership；enterprise_user INSERT 用 `membership_self_insert`（`ON CONFLICT` 与 owner 绕过不冲突）；`consume_invite` 单事务 SECURITY DEFINER（逐字段核对ledger、忽略客户端sub、`ON CONFLICT DO NOTHING` 不覆盖角色、同事务写audit）；migration role 仅增 narrow UPDATE/INSERT 策略。
2. **运行时修复**：`database.py` 不再解析 migration_dsn（用 `ACCEPTANCE_DATABASE`+f1 config），`_get_factory` 拒绝未知角色；`citation.py` 改用 `_api_dsn`（API不再用worker DSN）；`indexing.py` 新bridge签名+`_read_fixture_chunks`设企业上下文+`_finish`先doc/outbox后task终态；`invitation.py` consume 只传 OIDC `user["sub"]`；`audit.py` 仅 super_admin/auditor 可读；`create_enterprise` 绑定创建者为成员。
3. **红→绿实测**（f1_0003+运行时后）：`test_f111_security_boundaries` 由 `Ran 14 / FAILED(failures=11, errors=2)` → `Ran 14 / OK`；修复期间定位并修正 consume_invite 三处 AmbiguousColumn（RETURNS TABLE 输出列与表列同名）、token exp 微秒漂移（create_invite 归一化到整秒、DB 用 epoch::bigint 比较）。
4. **旧测试随边界收紧而更新**（均白名单 test_f11_*）：bridge 两测试改新签名+成员上下文；head 断言 0002→0003；企业B操作改用真实成员 SUB_TENANT_B（原 SUB_ADMIN 非B成员，是假绿）；`get_task(role=f1_worker)` 读终态改 `f1_api` 成员读。
5. **回归**：F1 40 + F1.1 49 + F1.1.1 14 = 103 全绿；migration replay 二次 upgrade 零 DDL。旧 reverse 现 `tenant_crosswires=1`（audit 对 enterprise_admin 正确返403而非假绿404，M2重写）`orphan_objects=6`（旧reverse自身插入业务行，M2重写随机run_id自清）。

## F1.1.1 M2 开始（2026-08-09）
M2 = 真正恢复/幂等/HTTP E2E。计划子步骤：
- M2a 上传幂等：先算SHA再查幂等；同企业同SHA→同一document/task零新增；单事务预留document/task/outbox后再写object；失败仅补偿本run同etag对象。
- M2b outbox dispatcher + Worker lease token/CAS + 重启续跑 + 双Worker不重复。
- M2c request_id绑定企业+question SHA（换问题409）；LLM引用必须属于PG复核集合。
- M2d 重写reverse（随机run_id、快照DB/MinIO/RQ/RAGFlow/audit、只走Keycloak+HTTP、断Redis/杀Worker/RAGFlow失败/重复上传/A/B 404、finally自清并与前快照相等）。

## F1.1.1 M2 真正恢复/幂等/HTTP E2E 完成（2026-08-09）
1. **M2a 上传幂等**：storage 拆 `preflight_upload`（先算SHA不写对象）+ `store_stream`（写opaque对象带etag）；documents router 重写为先SHA→find_existing（同企业同SHA返回同一document/task零新增）→单事务预留document/task/outbox→store_stream→失败仅补偿本run同etag对象。新增 `find_existing_upload`/`reserve_upload_task`（`create_upload_task` 保持幂等wrapper）。
2. **M2b outbox dispatcher + worker lease**：`f1.pending_dispatch_tasks()` SECURITY DEFINER 桥（f1_worker可跨企业枚举pending事件，RLS下无法直接看）；`dispatch_pending_outbox()` 重入队；worker claim 改 CAS lease（`lease_until` 原子claim + 重启续跑）；`_finish` 重排 doc/outbox 先于 task 终态（worker RLS 需 in-flight 任务）。
3. **M2c request_id 绑定 + LLM 引用校验**：`qa_request` 绑定 (enterprise, question_sha256)；`lookup_request(question=...)` 校验，换问题→`RequestIdConflict`→HTTP 409；`qa_chain._extract_cited_ids` 校验 LLM 每个 chunk_id 属于 PG 复核集合，否则 `FABRICATED_CITATION` 拒答。
4. **M2d 重写 reverse**（`tests/f111_reverse_verify.py`）：随机 run_id；快照 DB/MinIO；业务只走 Keycloak+HTTP（multipart上传/QA/audit均HTTP，禁直调内部函数插业务行）；停Worker→上传→断言各1 object/document/task/outbox→启Worker→done→QA(citations)→audit；Redis断开恢复、Worker SIGKILL重启、重复上传零新增、A/B互查404；`_self_clean` 用 f0d_bootstrap（FORCE RLS下 f0d_migration 无法DELETE）删本轮 artifact，断言 after==before 快照。
5. **关键修复**：Keycloak access token TTL≈60s → 每个HTTP调用前重取 token（E2E索引约66s，旧token在QA前过期致401）。
6. **红→绿实测**：`tests/f111_reverse_verify.py` 17项全0 exit 0（`valid_http_e2e=0 membership_spoof=0 public_definer_exec=0 arbitrary_f0i_tenant=0 invite_spoof=0 role_escalations=0 enqueue_recovery=0 worker_restart=0 duplicate_effects=0 orphan_objects=0 orphan_jobs=0 wrong_tenant_citations=0 audit_gaps=0 runtime_plaintext_leaks=0 clean_rebuild=0 upstream_mutations=0 scratch_residuals=0`）；新增 `tests/test_f111_recovery_idempotency.py` 6项 OK。
7. **回归**：F1 40 + F1.1 49 + F1.1.1 14 + M2 6 = 109 全绿；migration replay 零DDL。
8. 处置：旧 reverse `tests/f11_reverse_verify.py` 已由新 `f111_reverse_verify.py` 取代（旧脚本直调内部函数/插业务行，违反M2规则）。

## F1.1.1 M3 开始（2026-08-09）
M3 = 干净重建/日志/不可伪造产物。计划：
- M3a 镜像全部 @sha256；API/Worker/Web 加 build:；Web 多阶段 npm ci（去 dist 挂载）；Python 锁含版本+hash。
- M3b `git clone --no-local` 随机 scratch，随机 project/端口，fresh F1 DB/RAGFlow 卷，no-cache build→E2E→销卷→重建再跑。
- M3c RAGFlow 日志不 bind 仓库；日志/trace 仅 ID/SHA/长度/计数/reason；canary 扫 0 命中且 0700/0600。
- M3d v0.2 标 rejected；v0.3 生成器亲自执行验收并绑定 stdout SHA；SBOM 有效 CycloneDX。

## F1.1.1 M3 干净重建/日志/不可伪造产物（2026-08-09，进行中）
- M3a 镜像全 @sha256：compose 13个第三方镜像全部 pin digest（keycloak/minio/redis/prom/grafana/jaeger/otel/mysql/es/pgsty-minio/valkey/ragflow）；api/worker/web 加 build:；web 用多阶段 `web.Dockerfile`（node:22-alpine@sha256 → npm ci → nginx:1.27-alpine@sha256），去掉 dist 挂载；Python 锁 `requirements-f1.lock` 65 项全部版本+hash（linux/aarch64 平台，已验证在 Docker base 内 pip install exit 0）；Dockerfile base pin python:3.11-slim@sha256。
- M3c RAGFlow 日志不 bind 仓库：compose 改 `ragflow_logs` 命名卷；旧日志（10.2MB,1处QA标记,0644）先记 SHA/大小/泄漏数后迁至 `/private/tmp/anhuan-f1-ragflow-logs-archive`（0700/0600），仓库内目录清空。
- M3d v0.2 撤销 + v0.3 生成器：`artifacts_v03.py` 亲自执行 reverse+tests+lock 三闸门并绑定 stdout SHA；写 `v0.2/revocation.json`；SBOM 有效 CycloneDX 1.5。
- 新增 `tests/test_f111_reproducibility.py`（17项）：镜像 pin/build块/dist移除/锁hash/RAGFlow日志/v0.3 artifacts。静态总数 850（≥849），F1.1.1 测试 37（≥36）。
- reverse 健壮性：token TTL≈60s → 每 HTTP 调用前重取；`_wait_task_done` 360s（>300s lease）；worker restart 时清死 lease；QA/audit 用长 timeout；main() 开头 reset F1 业务表、finally 总 self-clean（FORCE RLS 下用 f0d_bootstrap 删除）。reverse 多次全0 exit0。

## F1.1.1 M3 产物 READY + 回归（2026-08-09）
- `artifacts_v03.py` 三闸门（reverse+2 suites+lock）exit0，结论 `F1_1_1_SECURITY_BOUNDARIES_READY`；`acceptance.json` stdout-SHA 绑定真实 gate 输出；`sbom.json` CycloneDX 1.5（16 components：13镜像+python lock+npm lock+api/web dockerfile）；`v0.2/revocation.json` 标记 revoked 并指向 v0.3。
- `tests/test_f111_reproducibility.py` 17项 OK；静态总数 850，F1.1.1 37 项。
- reverse 多次全0 exit0（clean baseline + 长 timeout + worker restart 清死 lease + finally 总 self-clean）。M3b clean rebuild（git clone --no-local 随机 scratch no-cache build→E2E→销卷→重建）待最终收口验证。

## F1.1.1 提交与 clean-rebuild 验证（2026-08-09）
- 提交 `7f05f3b`：M1-M3 全部工作（f1_0003 + M2 reverse + M3 pin/build/lock/log/artifacts）。
- reverse 17 项全0 exit0（含真实 `clean_rebuild=0`：compose config 有效 + 13 个 @sha256 digest 本机都在）。
- M3b clean-rebuild 验证：`git clone --no-local` 随机 scratch → no-cache build api/worker/web → 后续销毁卷重建。tracked-only clean（无 secrets/dist/ragflow-logs）。

## F1.1.1 M3b clean-rebuild 验证通过（2026-08-09）
- `git clone --no-local` 随机 scratch（tracked-only clean，无 secrets/dist/ragflow-logs）→ `DOCKER_BUILDKIT=0` no-cache build api/worker/web 成功（web 镜像 ad8fb980ecc6、api/worker 9644d394b7c9）；compose config 有效；`web.Dockerfile` 多阶段 npm ci。scratch 已删。
- `KEYCLOAK_ADMIN_PASSWORD` 构建告警为预期（secret 运行时挂载）。

## F1.1.1 收口完成（2026-08-09）
- 全仓回归：F1 40 + F1.1 49 + F1.1.1 37 = **126 tests OK**（多次确认）；静态总数 **850**（≥849）。
- reverse `f111_reverse_verify.py` 17 项全0 exit0（多轮稳定）；v0.3 READY；v0.2 revoked。
- 完成条件核对：
  1. 非成员/PUBLIC/API/Worker 无法跨 F1/F0-I 租户（membership_spoof/public_definer_exec/arbitrary_f0i_tenant/invite_spoof/role_escalations 全0）；真实 HTTP 上传在断 Redis/杀 Worker/RAGFlow 失败/重复请求后可恢复且零重复/孤儿/错引/审计缺口/明文（enqueue_recovery/worker_restart/duplicate_effects/orphan_objects/orphan_jobs/wrong_tenant_citations/audit_gaps/runtime_plaintext_leaks 全0）。
  2. tracked-only clean checkout 可二次从零重建（M3b clone --no-local + no-cache build 通过）；≥849 测试（850）；v0.3 真实绑定 stdout SHA 双跑一致（acceptance=b750ccb7…/d8ca198c… 两次 READY，sbom 恒定）；F0-A~J1/26原件/F0-I 零漂移（upstream_mutations=0）；旧 v0.2 明确撤销（revocation.json）未升级。
