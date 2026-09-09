# 候选环境一次性运维检查

`scripts/candidate_ops_check.py` 对一个明确选择的本地候选执行只读检查，输出私有 JSON 回执。检查覆盖已绑定容器的健康状态和重启次数、当前数据库迁移与 FORCE RLS、七类持久任务/投递聚合、API 五项依赖就绪状态，以及可选的对象对账计划引用。脚本不注册定时任务、不发送通知、不启动或停止服务、不恢复任务、不执行对象修复。

未提供配置时返回 `NOT_TESTED`，不会自动寻找共享栈、推测部署参数或读机器上的其他凭据。一次通过只证明本次观察，不证明持续可用性、告警送达、真实材料质量或生产验收。

## 选择目标与运行

从本次候选的创建回执、实际 Docker inspect 和数据库只读身份查询取得目标值。支持专用名称 `anhuan-ar-uat-<12位hex>`、`anhuan-ar-pgint-<12位hex>` 或 `anhuan-ops-candidate-<12位hex>`；数据库名必须为 `f1_arpg_<12位hex>`。这不是通用生产监控入口。

配置、DSN、对象计划文件均须为当前用户拥有、单一硬链接、无符号链接的 0600 普通文件。运维凭据沿用现役独立维护环境中的 `f0d_bootstrap`，不分发给 API 或任何 worker，也不新增数据库权限。DSN 仅接受 `host`、`port`、`dbname`、`user`、`password` 和可选 `sslmode`；host 必须为 `127.0.0.1`，端口必须与本次 PostgreSQL 容器的实际 loopback 发布端口相同。运行连接同时固定 hostaddr，防止继承的 `PGHOSTADDR` 改变连接目标。

以下配置中的尖括号全部替换为当前实测值。容器 ID 是完整 64 位 hex；镜像 ID 为 `sha256:` 加 64 位 hex，不能用可变标签。`migration_source_sha256` 来自现役 `candidate_backup.migration_identity()`，覆盖迁移源码；服务清单至少包括下面十二项，可加入本次候选实际使用且具备健康检查的其他持续服务，不加入一次性初始化任务。

```json
{
  "schema_version": 1,
  "scope": "local_candidate",
  "candidate_id": "<本次候选的ASCII标识>",
  "compose_project": "anhuan-ar-uat-<本次12位hex>",
  "docker_host": "unix:///<本次Docker socket绝对路径>",
  "migration_source_sha256": "<当前迁移源码SHA256>",
  "database": {
    "dsn_file": "/PRIVATE_OPS/current-bootstrap-dsn",
    "name": "f1_arpg_<本次12位hex>",
    "cluster": "<pg_control_system的system_identifier十进制字符串>",
    "head": "f1_0044"
  },
  "services": {
    "postgres": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "minio": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "redis": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "clamd": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "keycloak": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "api": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "web": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "worker": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "ingestion-worker": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "report-worker": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "dispatcher": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"},
    "source-gateway": {"container_id": "<实测64位hex>", "image_id": "sha256:<实测64位hex>"}
  },
  "thresholds": {
    "window_seconds": 3600,
    "pending_age_seconds": 300,
    "retry_due_age_seconds": 120,
    "expired_lease_grace_seconds": 120,
    "high_attempt_threshold": 3,
    "max_recent_terminal_failures": 0,
    "max_retry_wait": 20,
    "max_high_attempt_active": 0,
    "max_overdue_pending": 0,
    "max_overdue_retries": 0,
    "max_expired_leases": 0,
    "max_service_restarts": 3,
    "object_plan_max_age_seconds": 3600,
    "max_object_issues": 0
  },
  "object_plan": null
}
```

阈值必须全部显式配置，上面是本地工程演练示例，不是正式 SLA 或供应商承诺。检查器不会用示例值默默填补缺失项。若演练要求出现一个等待重试任务即报警，可把 `max_retry_wait` 设为 `0`；示例值 `20` 不会仅因出现四个 retry_wait 就报警。

安装当前锁定的 Python 依赖，从仓库根运行；输出使用新的绝对路径，现有回执不会被覆盖：

```sh
python -B scripts/candidate_ops_check.py \
  --config /PRIVATE_OPS/candidate-config.json \
  --output /PRIVATE_OPS/NEW-ops-result.json
```

退出码 `0` 对应 `PASSED`；`1` 对应 `ALERT` 或 `FAILED`；`2` 对应 `NOT_TESTED`。所有检查执行后，总状态优先级为 `FAILED > ALERT > NOT_TESTED > PASSED`。目标配置、容器身份或 DSN 绑定失败时立即停止；数据库观察失败后仍可记录已绑定 web 的就绪检查和对象计划引用，不能由其他通过项冲淡失败。

## 指标解释

Docker 只读取配置中容器的 ID、镜像、project/service 标签、运行/健康状态、重启次数和端口映射，不读取环境变量或健康日志正文。容器必须与配置完全匹配；PostgreSQL 和 web 分别要求 5432/80 的唯一 `127.0.0.1` 发布端口。缺失健康检查、starting、unhealthy 或未运行都会报警；重启次数是当前容器生命周期累计值，不是最近一小时发生率。

数据库要求 PostgreSQL 18、`f0d_0006 / f1_0044` 和当前受保护集合中的 55 张表仍启用 ENABLE/FORCE RLS。查询在 `REPEATABLE READ READ ONLY` 事务内执行，结束时 rollback；单条语句最多 3 秒、锁等待最多 1 秒。既有维护角色须确实拥有全局可见性，且设置 `row_security=off`：不能把被 RLS 隐藏的零行当作健康，也不会通过扩大运行角色权限绕开此检查。

聚合对象为三张 delivery 表、`material_evidence_job`、`material_rag_job`、`analysis_report_generation_job` 和 `upload_task` 的 `controlled_ingestion` 子集。输出只有表名、闭集状态和数量/年龄，不含租户或任务 ID、正文、对象 key、actor、令牌、错误详情。各表分别应用阈值，没有把不同阶段同一份材料的计数相加当成独立任务总数。

| 指标 | 报警含义 |
| --- | --- |
| `terminal_failures_total` | 累计 blocked/failed，只供观察，不直接报警。扫描按规则 rejected 不算系统失败 |
| `recent_terminal_failures` | 仍处于失败终态且 `updated_at` 在显式窗口内的数量，超过上限报警；它不是追加式事件流，已恢复任务不会继续计入 |
| `retry_wait` | 当前等待重试数量超过上限 |
| `overdue_retries` | `next_attempt_at` 已超出重试宽限，且数量超过上限 |
| `overdue_pending` | pending/queued/received 的 `updated_at` 年龄超过配置阈值，且数量超过上限；不是端到端耗时 |
| `expired_leases` | 活动任务租约已过期，原始数量仅观察 |
| `expired_leases_past_grace` | 过期时间超过宽限，且数量超过上限 |
| `high_attempt_active` | 仅活动任务的 attempt 达到阈值，且数量超过上限；已完成或失败终态不重复误报。报告 generation job 无 attempt 列，因此不生成该表的 attempt 告警 |

未知任务状态会使检查失败，不忽略新状态。API 检查调用已绑定 web 的 `/api/readyz`，严格读取现役 database/minio/redis/clamd/oidc 五个布尔结果；缺项、类型不符或 HTTP 与正文矛盾均失败。此请求不调用 OCR/模型，不验证所有异步 worker 的完整材料处理能力。

## 对账与恢复入口

`object_plan=null` 时对象项为 `NOT_TESTED`，即使其他三项通过，总状态也保持 `NOT_TESTED`。需要对象证据时先按 [OBJECT_RECONCILE.md](./OBJECT_RECONCILE.md) 在同一候选执行现有只读 `plan`，再填入：

```json
"object_plan": {
  "path": "/PRIVATE_OPS/current-object-plan.json",
  "sha256": "<整个文件原始字节的SHA256>",
  "storage_identity": "<该候选存储endpoint与账号的现役身份SHA256>"
}
```

文件 SHA、计划自身 digest、数据库名/集群/head/存储身份和创建时间均需匹配。回执明确写 `REFERENCED_READ_ONLY_PLAN_NOT_RESCANNED`，保留计划创建时间、年龄与摘要；监控不会把旧计划表述为本次重新扫描的对象结果。超过配置有效期或未来时间失败；只输出问题 code 的数量，不带 issue 细节。

| 发现 | 处置入口 |
| --- | --- |
| 服务未就绪、重启次数超限 | 检查本次候选的服务依赖和私有日志；根因修复后重新观察。监控回执没有启动/重启权限 |
| retry、过期租约、pending 超限 | 先修依赖，再观察正常 dispatcher/worker 是否重新领取；不手改任务状态或租约，不把被旧令牌拒绝当成可绕过保护 |
| 原生任务 blocked | 由当前有效管理员使用现有显式恢复入口；继续保留请求回执、成员状态和任务租约检查 |
| 对象缺失或疑似残留 | 阅读绑定计划，根据 [OBJECT_RECONCILE.md](./OBJECT_RECONCILE.md) 决定恢复、保留或人工检查；本脚本不执行 `apply` |
| 数据损坏、迁移/密钥异常 | 使用 [CURRENT_HEAD_RECOVERY.md](./CURRENT_HEAD_RECOVERY.md) 的当前 head 备份核验与独立空环境恢复，禁止借用旧 head 恢复证明或做 downgrade |
| OCR 成本或重复请求异常 | 对照 [OCR_CACHE.md](./OCR_CACHE.md) 的完整身份、缓存与现役提供方调用证据；此监控不计费、不调用真实供应商，也不证明 OCR 质量 |

## 验证与交接边界

定向验证命令：

```sh
PYTHONPATH=src:. python -B -m unittest \
  tests.test_candidate_ops_check tests.test_candidate_ops_postgres -v
```

2026-09-09 本地定向验证共 17 项通过：12 项离线边界检查和 5 项专用 PostgreSQL 18 检查。实库已观察到已知 retry/blocked、pending 超时与过期租约；验证历史终态不误报、角色可见性收窄时拒绝、只读事务拒绝写入、错误集群/head 拒绝，以及业务行/审计不变。独立栈清理为 `CLEAN`，专用容器/网络/卷均为 0，共享指纹不变。此17项定向记录本身不包含候选十二服务联动检查。随后0044冻结候选的browser统一门实际运行十二服务：正常时services/database/readiness均PASSED，Redis中断时三者均ALERT；未配置对象计划，objects仍NOT_TESTED，正常总状态亦NOT_TESTED。原四个上传投递在Redis与worker恢复后均以相同ID完成。证据为仓外out/phase1_material_stability_2026-09-09/final_frozen/20260909T011819Z-browser-a037d05f/的browser-result.json、ops-baseline.json与ops-queue-outage.json；总索引为current_priority_result.json。专属资源及本次镜像标签清理完成，共享指纹不变。这是本地隔离候选监控证明，不是目标环境或通知触达证明。

正式运维交接仍需明确实际服务器与部署负责人、值班与通知接收人/渠道、业务峰值及阈值、备份周期/保留地点/保留期、允许的数据损失与恢复时限（RPO/RTO）、存储/OCR/模型预算，以及对应真实演练证据。在这些输入落地前，回执始终保留 `production_deployment=NOT_TESTED`、`alerts_delivered=NOT_TESTED`、`formal_sla=NOT_CONFIGURED`；本地示例阈值不能替代这些决定。


2026-09-09补充：[2297e3d远端CI](https://github.com/leoli001031-blip/anhuan/actions/runs/34332728730)已在GitHub Ubuntu 24.04实际完成相同的十二服务观察、Redis故障告警和原四个delivery重启恢复。browser 29项、integration 197项、restore 1场景及offline均通过；完整回执已取回仓外remote_ci/run-34332728730。此项关闭S07远端CI缺口，正式部署、真实通知送达及正式SLA仍维持上述未验状态。
