# P3 CONTROLLED INGESTION Progress

> **现役摘要（2026-08-11）：** `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`。当前迁移 head 为 `f1_0010`，代码/证据 checkpoint 为 `9d712cd`；下文保留启动时状态与过程。总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 安全换挡

- 已创建本地checkpoint `4180709`保存暂停的F1.1.1修复和已完成P2；未push、未部署，工作树干净。
- 当前唯一F1迁移head为`f1_0005`；P3固定新增`f1_0006_controlled_ingestion`，不修改旧迁移。
- 已完成现状只读勘察：现有上传会直接写正式bucket并进入旧Fixture索引链，缺恶意扫描、逻辑版本、quarantine和安全预览，不能直接视为P3。
- 已冻结P3数据/API/UI/权限/资源限制和三线并行地界。状态：`NOT_TESTED`。
- P4至P8保持阶段串行；当前仅P3允许写实现。

## 启动时计划（已完成）

1. 已创建线性`f1_0006`并同步models、quarantine/preview storage与API contracts。
2. 已实现P3后端feature/router、文档库/详情/预览前端和22项轻量离线合同测试。
3. 已选择安全原型路径：上传只到`quarantined + held`；显式处理动作使用本地scanner，clean后才CAS到`ready + held`；人工release再进入`released`。P3不写outbox、不触发旧OCR/RAGFlow/indexing。
4. 已接API main、App/Layout、受控文档导航与显式“开始安全处理”按钮；当前继续做静态收口与文档同步。

## 2026-08-10 P3安全接缝收口

- scanner不可用、超时或协议异常一律持久化为`retry_wait + held`，不产生假clean；infected/不可恢复结构错误进入`failed + blocked`。
- 安全预览使用确定性对象键和canonical manifest：PDF/DOCX为分页纯文本JSON、XLSX为不求值公式的受限grid、JPEG为剥APP/COM后的JPEG代理。
- 扫描/预览成功时任务同时变为`status=done + object_state=ready + quarantine_status=held`，且不存在P3 outbox，因此旧worker没有可消费事件；人工release使用`ready + held + clean + preview ready`严格CAS。
- 当前只完成一次Python编译级最小检查；后续集成改动尚未跑P3定向套件或前端build，阶段验证状态仍为`NOT_TESTED`，不得写`RELEASE_VERIFIED`。

## 2026-08-10 连续阶段授权

- 用户已明确授权继续当前P3实际开发；P3完成后无需再次确认，严格串行推进P4→P5→P6→P7→P8，阶段内部可按不重叠文件地界使用subagent并行。
- 每阶段必须先创建并持续维护独立TASKBOOK/PROGRESS/BLOCKED；migration链、models、API main、App/Layout和lockfile继续由主agent单写，禁止并行Alembic head。
- 采用轻量原型验证预算：每阶段最多一个预计不超过60秒的直接相关最小检查；不主动新增无关测试，不跑全仓/E2E/coverage/benchmark/生产构建/视觉回归。未验证必须标`NOT_TESTED`。
- 不恢复F1.1.1 formal/reverse/SBOM/clean/M4；不commit、不push、不部署、不删除共享数据。checkpoint `4180709`保持不变，后续commit需另行授权。
- 仅立即修当前阶段无法启动/编译、主链阻断、数据损坏、明确跨租户越权或migration head冲突；其余登记技术债。
- P8完成后停止并汇总，不进入真实UAT、生产或正式小程序发布。

## 2026-08-10 P3实现收口

- 已修复最终静态复核发现的四个主链阻断：Compose新增仅内网可见、关闭在线签名更新的固定ClamAV 1.4.6 arm64 sidecar；产品导航中的旧上传入口改为跳转受控文档；P3预留/写失败状态不再产生无法序列化的`not_applicable`；reject现在要求`held`并以`RETURNING`确认真实状态变化后才写源状态和审计。
- 官方ClamAV镜像固定为`clamav/clamav:1.4.6-debian13-slim`的arm64内容摘要；无host端口、无外部扫描服务、API仅在sidecar健康后启动。
- P3实现主体已覆盖文档库、独立版本、quarantine、显式本地扫描、安全预览、重试、拒绝和人工release；不写旧outbox，不触发OCR/RAGFlow/indexing。
- 阶段实现状态：`P3_COMPLETE_NOT_RELEASE_VERIFIED`。本阶段后期集成未追加第二次检查，当前验证标签仍为`NOT_TESTED`；未启动ClamAV、数据库、对象存储或浏览器链，不把静态收口写成运行通过。
- 按用户连续授权，P3停止扩项并立即进入P4；F1.1.1发布验收保持暂停。

## 2026-08-11 正常验证轮

- 用户授权后先创建本地保存性checkpoint `06f0500`；未push、未部署。随后验证修正与runner仍保留在工作树，未追加commit。
- P3-P8联合定向合同回归`Ran 58 / OK`，其中P3的格式、scanner协议、迁移/RLS和显式处理边界共22项全绿；Alembic唯一head为`f1_0010`。
- 新增并运行UUID隔离的P3真实smoke：随机PostgreSQL、MinIO、ClamAV固定digest容器，tmpfs且无共享volume；真实完成合成PDF上传→ClamAV INSTREAM扫描→安全预览→人工release→B租户404。
- 结果`P3_REAL_INGESTION_SMOKE_PASSED_NOT_RELEASE_VERIFIED`，migration/sidecar/MinIO/scanner/upload/process/preview/release/cross-tenant/data identity/cleanup/unexpected共12项聚合指标全0。
- 当前标签更新为`P3_COMPLETE_NOT_RELEASE_VERIFIED + SMOKE_PASSED`；这仍不是F1.1.1发布验收、真实客户数据验证或生产就绪。
