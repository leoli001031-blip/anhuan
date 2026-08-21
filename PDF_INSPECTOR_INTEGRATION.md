# PDF Inspector 受控纳入决策

决策日期：2026-08-12

```text
ARCHITECTURE_CONSIDERED
VENDOR_NEUTRAL_SHADOW_SEAM_IMPLEMENTED_NOT_TESTED
RUNTIME_DISABLED
NOT_PRODUCTION
```

## 结论

PDF Inspector 已作为“降低 PDF 材料录入成本”的候选影子解析器纳入供应商中立合同，但运行实现仍固定关闭：当前分支不安装它，也不把旧 Probe 的源码、依赖或运行产物并入主 API、worker 或常驻镜像。

当前材料录入切片由已锁定的 `pypdf` 生成文本/扫描、表格、双栏和法规/报告字段候选；页面显示页码证据与未校准置信线索，人工确认后才创建政策来源和版本草稿。该能力不依赖 Inspector，也不把候选自动写成权威数据。

未来若满足全部供应链与隔离门，它只能在 P3 对源对象完成身份核验且 ClamAV 判定 `clean` 之后，以默认关闭、进程外、无网络的 shadow 任务运行。现有 P3 与 `pypdf` 继续承担权威解析和 fallback；Inspector 输出只能形成待人工确认的草稿，不得直接改变文档状态、证据、报告、法规、索引或问答结果。

## 采用与不采用的部分

采用的是旧 PDF Probe 已验证过的架构思想：

- 单文件、单进程、进程外执行；固定超时、内存、输入和输出上限。
- 不允许网络、OCR、外部 provider、数据库凭据、MinIO 凭据或 OIDC secret 进入 runner。
- `pypdf` 的页数、页序和几何判断不被候选结果覆盖。
- 输出使用固定 reason code；正文不得进入日志、trace、命令行、审计详情或公开产物。
- 候选结果必须可关联到精确 `document_version` 与源对象 SHA-256，源身份不匹配时拒绝处理。

不采用的是旧 Probe 的实现和运行依赖：

- 旧 `codex/f1-1-2-pdf-probe` worktree 继续只读，不 merge、rebase、cherry-pick 或复制其未提交历史。
- 旧 Probe 的 21 份 PDF、248 页双重放只证明隔离 shadow 机制曾在登记 Fixture 上可运行；其标签是 `SHADOW_PROBE_COMPLETE_NOT_EVALUATED`，没有证明准确率、Gold、真实客户材料或生产可用性。
- `pdf-inspector 0.2.6` 发布包依赖 `lopdf 0.41.0`，受 `RUSTSEC-2026-0187` 影响；该版本及其 wheel、sdist、缓存和旧 runner 产物均不得进入当前 API、worker、镜像、上传链或任何常驻运行环境。

## 目标数据流

```text
P3 上传到 quarantine
  -> 校验对象 SHA-256 / size / etag
  -> ClamAV 扫描
  -> 非 clean：停止，不调用 Inspector
  -> clean：现有 pypdf 生成权威预览
  -> [默认 OFF] 进程外 Inspector shadow
  -> 候选页文本 / 版式 / OCR 提示草稿
  -> 人工确认、编辑或丢弃
  -> 只有后续单独授权的工作流才能使用已确认内容
```

ClamAV `clean` 是启动解析的必要条件，不是文件无风险或候选内容正确的证明。Inspector 不得读取 quarantined、扫描中、infected、scanner unavailable、身份不一致或跨租户对象。

## 权威、草稿与 fallback

- P3 的源对象 SHA-256、大小、etag 和版本关系是文件身份边界。
- `pypdf` 保持 PDF 页数、页序、页面几何、默认页文本预览和解析失败判断的权威来源；如未来需要新几何字段，也以 `pypdf` 结果为准。
- Inspector 只返回 `CANDIDATE`：候选页文本、重复绘字、双栏、表格、乱码和 OCR 建议信号。它不得覆盖 `pypdf` 页映射或改变 P3 状态机。
- Inspector 超时、崩溃、输出超限、合同不匹配或依赖不可用时，丢弃该次候选并记录固定 reason；现有 `pypdf` 预览继续可用，不把 shadow 失败写成 P3 主链失败。
- 候选正文若在未来获准落库，必须存为租户隔离、版本绑定、可追溯的“未确认草稿”，不得进入 canonical evidence、报告快照、法规正文、搜索索引、RAG/QA、OCR 路由或外部通知。
- 人工确认必须显式记录确认人、时间、源版本、候选摘要和确认后的内容摘要；未确认、编辑中或被丢弃的结果没有业务权威性。

## 运行隔离合同

未来 runner 必须同时满足：

1. 默认 feature flag 为 OFF；未配置或异常一律视为 OFF。
2. 只接受 P3 在同一租户内签发的短生命周期任务描述，且再次核验版本、SHA-256、size 与 `scan_verdict=clean`。
3. 使用独立进程或容器；网络 deny、只读根文件系统、非 root、无宿主路径、无 Docker socket、无数据库/对象存储/OIDC secret。
4. 每次只处理一个 PDF；固定 wall time、RSS、文件大小、页数、文本项、字符数和标准输出上限。
5. 输入通过受控只读 fd/pipe 或一次性私有 scratch；scratch 目录 0700、文件 0600，结束后按精确身份清理并核残留为 0。
6. 标准输出只允许有界、带 schema version 的结构化结果；stderr、日志和 audit 只允许固定 reason 与聚合计数。
7. 结果写入前再次比对源版本和租户；任何漂移、过期或重复任务都幂等拒绝，不能覆盖人工确认结果。
8. runner 失败不得把源对象标成 ready、released 或已确认，也不得改变现有 `pypdf` 预览。

## 供应链启用门

启用前必须使用不受该 advisory 影响的 patched pinned build。最低要求：

- `lopdf` 实际解析依赖达到已修复范围，不接受仅修改版本字符串或在上层捕获异常。
- 固定源仓库 commit、Cargo/Python lock、平台构建产物 SHA-256、许可证和完整依赖 SBOM；离线、`--no-deps`/等价方式从受控 wheelhouse 或本地构建材料安装。
- 对实际产物重新核 `RUSTSEC-2026-0187` 与当时有效的其他 advisory；结果有未豁免高风险项时保持 OFF。
- 不复用 0.2.6 的旧二进制、缓存、wheelhouse、签名或“曾跑通 Fixture”结论作为补丁证据。
- 独立定向检查证明加载的是被固定的 patched native binding，而不是系统包、用户 HOME 或旧 worktree 中的模块。

满足供应链门只代表可以进入合成 shadow 验证，不代表可以接任意上传或真实客户数据。

## 分阶段启用方案

### Gate 0：当前 Inspector 状态

- 已有默认关闭、无供应商依赖的 shadow capability seam；它只返回固定 disabled 状态。
- API、worker、Compose、requirements、lockfile 均未安装或 import Inspector；页面只显示 shadow 已关闭。
- 状态保持 `RUNTIME_DISABLED / NOT_PRODUCTION`。

### Gate 1：patched runner 原型

- 单独任务书和文件地界；只处理合成 PDF。
- 验证供应链、无网、资源上限、正文不落日志、失败清理和确定性。
- 任一门失败即停止，不接 P3。

### Gate 2：P3 clean-only shadow

- 仅在隔离环境的合成/内部非客户材料上，由显式操作触发。
- Inspector 结果不展示为权威内容，不改变 P3 主链；比较 `pypdf` 与候选的页级差异和失败率。
- 验证跨租户详情 404、列表零行、幂等、重启恢复、runner 不可用和任务过期。

### Gate 3：人工确认草稿

- `pypdf` 路径已实现最小数据模型、FORCE RLS、allowed_actions 与人工编辑确认页面，状态以 `MATERIAL_INTAKE_PROGRESS.md` 为准。
- source + version draft + analysis confirmed + audit 在一个事务提交；不会自动审核、发布或解除隔离。
- 这不代表 Inspector Gate 1/2 已通过；真实客户数据、OCR、自动晋升、索引/RAG 和自动创建报告仍需新的明确授权。

## 后续验收条件

只有以下全部成立，才可把 Gate 2 标为可用 shadow；否则保持 OFF：

- patched pinned build 的来源、hash、lock、SBOM 与 advisory 检查可重放。
- ClamAV 非 clean 或不可用时 Inspector 调用数为 0。
- 进程外无网、无 secret、资源上限和 scratch 残留反测通过。
- `pypdf` 权威字段覆盖次数为 0；Inspector 失败时现有 P3 happy path 不退化。
- 跨租户 API、任务与候选结果泄漏均为 0。
- 任意候选进入 canonical evidence、索引、报告、法规、RAG/QA 或外部通知的次数为 0。
- 草稿正文、文件名、对象 key、路径、凭据和 PII 的日志/argv 泄漏为 0。
- 人工确认页面尚未实现时，候选正文不得作为产品数据对外可见。

## 当前完成定义

当前只把供应商中立的 disabled shadow seam 与 `pypdf` 人工确认录入切片纳入代码；没有安装 Inspector、启动旧 formal、运行旧 Probe 或处理真实材料。因此只能写成“Inspector 已纳入架构考虑且运行时关闭”，不能写成“Inspector 解析器已启用”。
