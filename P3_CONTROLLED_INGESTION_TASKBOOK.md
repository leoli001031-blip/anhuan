# P3 CONTROLLED INGESTION 任务书

> **已完成的历史执行合同。** P3 当前为 `P3_COMPLETE_NOT_RELEASE_VERIFIED / SMOKE_PASSED / NOT_PRODUCTION`；现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。下文的 checkpoint、队列和提交限制保留其启动时语义。

## 状态与边界

- P2已完成`TARGETED_TEST_PASSED + SMOKE_PASSED`，但不是发布验收；F1.1.1继续保持`F1_1_1_PAUSED_NOT_ACCEPTED`。
- P3在本地checkpoint `4180709`之上开发；不push、不部署，未经另行授权不再commit。
- 迁移唯一线性前进：`f1_0006_controlled_ingestion`，`down_revision=f1_0005`。`f1_0001`至`f1_0005`只读，禁止第二Alembic head。
- P3只做受控文档进入、版本、quarantine、本地恶意扫描、安全预览、失败恢复和资源上限；不做正式报告、法规发布、Gold/准确率、外部通知、生产部署或正式小程序。
- 不删除共享对象或历史材料；拒绝/隔离只改变状态并保留审计记录。

## 产品闭环

创建逻辑文档 → 上传首版本到quarantine → 本地恶意扫描 → 结构/资源校验 → 生成安全预览 → 人工释放到内部文档库 → 上传新版本 → 失败重试或拒绝保留。

## Task 0：合同冻结与并行地界

- 复用现有`document/upload_task`和lease字段，但P3原型不写outbox、不进入旧dispatcher/Worker；由显式`process`动作驱动本地扫描与预览，不另造第二套任务队列。
- 新增`document_record`、`document_version`；在`f1_0006`内为P3扩展现有任务的pipeline/stage/scan/preview元数据。
- P3产品入口只允许PDF、DOCX、XLSX、JPEG；旧Fixture索引入口不得成为绕过扫描的产品入口。
- P3文件先写独立quarantine bucket；只有扫描clean、结构校验和预览完成后才允许release。P3 release不自动触发OCR、RAGFlow或canonical证据变更。
- 主agent单写任务文档、migration、models、现有共享核心文件、API main/App/Layout、依赖与compose；后端/前端/测试subagent只写各自新增目录，发现共享接缝只回报主agent。
- 阶段串行：P3未完成前，P4至P8只能只读勘察，不得创建迁移或写实现。

## Wave 1：文档库与版本上传

- 数据：逻辑文档、递增版本、opaque幂等键、复合租户外键、创建人和最新版本摘要。
- API：能力、文档列表/详情、创建首版本、追加版本；跨租户统一404或零行。
- 上传后固定为`quarantine=held`、`scan=queued`、`preview=blocked`，不直接进入旧索引链。
- 页面：文档库、能力/资源限制卡、上传首版本、文档详情、版本列表、新版本上传；含空/加载/错误状态和企业切换刷新。

## Wave 2：本地扫描与资源闸门

- 本地scanner使用无外部调用的受限链；目标为ClamAV INSTREAM sidecar，镜像/签名固定、无host端口、运行时自动更新关闭。scanner不可用必须fail-closed并进入可重试状态。
- 拒绝EICAR/命中病毒、MIME与magic不一致、加密PDF、宏/外部关系、ZIP路径穿越/嵌套归档/异常压缩比、损坏容器和资源超限。
- 初始上限：PDF 50MiB/128页；DOCX/XLSX 25MiB；JPEG 20MiB/4000万像素；OOXML 2048 entries、单entry 16MiB、解压总量128MiB、压缩比100:1。
- 状态主链：`received→scanning→validating→previewing→ready`；失败为`retry_wait/rejected/failed`，最多3次；infected永不允许release。

## Wave 3：安全预览与失败恢复

- PDF/DOCX输出分页低保真纯文本JSON预览；XLSX输出受限worksheet grid；JPEG只返回服务端验证后的代理内容。浏览器不执行SVG、HTML、宏、公式或文档主动内容。
- API不返回object key、etag、presigned URL、绝对路径、解析器命令或scanner原始响应；reason只用固定code。
- 分页文本与表格预览单元最多256KiB/10万字符；经校验并剥元数据的JPEG代理最多20MiB，且单边不超过10000像素、总像素不超过4000万；扫描60秒、解析30秒；外部关系和DTD/ENTITY不解析。
- 显式处理器复用task lease字段与CAS；恢复只接受同SHA/size对象，过期处理token不得提交。retry只允许可恢复reason且未超过3次。后台异步调度延后，不以放宽quarantine边界换取自动处理。
- 页面展示扫描、quarantine、preview、固定失败原因、重试按钮及分页/表格/图像预览。

## Wave 4：释放、审计与集中收口

- 只有`scan=clean && preview=ready && quarantine=held`可release；release仅进入内部文档库，不触发P3外处理。
- retry/reject/release及关键状态变化与audit同事务；列表与详情由服务端`allowed_actions`决定按钮。
- 旧`/documents/upload`必须收缩为内部Fixture兼容或转入P3受控链，产品UI不可绕过。
- 完成后集中去重问题，先修当前P3主链阻断、数据损坏、明确跨租户越权和migration冲突；其余技术债登记后按用户授权串行进入P4。

## 权限合同

- `super_admin/enterprise_admin/plant_admin`：创建、追加版本、查看、重试、release/reject。
- `partner/auditor`：P3暂不获得全库读取；后续只能经明确业务附件授权。
- 所有新表`ENABLE + FORCE RLS`；详情在tenant session下按opaque ID查询，不可见统一404。
- 显式处理器只能凭精确task ID、处理token和未过期lease更新对应版本；P3不向旧Worker授予新的入口。
- PUBLIC无表写权；正文、文件名、路径、object URL、token/DSN/key不得进入日志、artifact或audit。

## 并行文件地界

- Backend subagent仅新增`src/platform_foundation/f1/features/p3/**`与`src/platform_foundation/f1/api/routers/p3_controlled_ingestion.py`。
- Frontend subagent仅新增`src/web/src/features/p3/**`。
- Test subagent仅新增`tests/test_p3_*.py`、`tests/p3_*_smoke.py`、`tests/fixtures/p3/**`。
- 主agent单写`f1_0006`、`models.py`、现有storage/upload/worker文件、API main、App/Layout、compose/locks和三份P3文档。

## 验证与状态标签

- 每Wave最多一个P3后端定向命令、一个前端目标build、一次该Wave主链冒烟。
- 最终只运行P3定向套件、前端build和一个UUID隔离scratch主链；禁止恢复F1.1.1 formal/reverse/SBOM/双轮clean/全仓discover。
- 反向最低覆盖：格式/magic、恶意样本、资源上限、scanner unavailable、幂等版本、stale retry、release闸门、跨租户404/零行、P3不触发OCR/RAGFlow。
- 状态仅：`NOT_TESTED`、`SMOKE_PASSED`、`TARGETED_TEST_PASSED`、`P3_COMPLETE_NOT_RELEASE_VERIFIED`；不得使用`RELEASE_VERIFIED`。
