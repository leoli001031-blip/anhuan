# 当前材料候选统一验收

在仓库根目录、安装锁定 Python 依赖后运行。证据目录建议放在仓外；每次运行生成独立目录，不覆盖旧失败。

```sh
python -B scripts/acceptance_gate.py --mode offline --evidence-dir /absolute/evidence
python -B scripts/acceptance_gate.py --mode integration --evidence-dir /absolute/evidence --timeout 1800
python -B scripts/acceptance_gate.py --mode restore --evidence-dir /absolute/evidence --timeout 1800
python -B scripts/acceptance_gate.py --mode browser --evidence-dir /absolute/evidence --timeout 1800
python -B scripts/acceptance_gate.py --mode quality --quality-manifest /absolute/materials/manifest.json --evidence-dir /absolute/evidence
```

`offline` 运行声明的 Python 与前端逐例检查、lint/build。`integration` 使用真实隔离 PostgreSQL、MinIO、Redis 和受限子进程。`restore` 备份当前 head 的完整候选，删除独占源栈后恢复到全新栈，并以恢复后的受限角色读取业务内容。

`browser` 新建随机命名的独立 Compose project，使用实际 Linux worker、ClamAV、数据库和对象存储，启动独立 Chrome profile，经 OIDC 登录并操作材料界面。只预置合成身份、客户、服务及空材料范围，材料由浏览器上传。PDF/JPEG OCR 使用独立 TLS 服务，仅匹配本次已生成页面/图片的渲染 SHA；运行时校验测试证书，未关闭 TLS 校验。这个服务及测试 CA 只存在于本次隔离栈，不修改部署 Compose 和生产信任库。该模式不调用收费模型，也不代表真实识别质量。

浏览器模式需要 Docker Compose、可构建锁定运行镜像的网络和系统 Chrome/Chromium、Node。它不依赖 ARM64 私有 OCR 镜像。每次保留 Compose 和浏览器日志、输入 SHA、逐项旅程检查，并核对受限后台卷不含 API/存储密码及 OCR 每个输入的实际 HTTP 次数。报告现役合同要求客户材料与服务商共享材料同时存在；运行器会从共享资料界面另外上传合成方法说明，不预置可用证据或把客户检测值复制到共享域。失败后的剩余检查为 `not_run`。资源清理限定本次随机 project，核对容器、卷、网络为零并核对共享资源指纹；清理失败不会返回通过。运行器收到可处理的终止信号会尝试清理；主进程被 SIGKILL 或主机断电不承诺自动清理，应按回执的确切 project 进行检查，不可按前缀批量清理。

`quality` 是真实材料及人工金标入口；提供 `--quality-live-ocr` 时才使用已配置云 OCR。未开启时不自动调用环境中的模型。未提供输入返回 `NOT_TESTED`，不会代为运行合成样本。[质量输入合同](MATERIAL_QUALITY_GATE.md)描述格式、精确比较与 OCR 边界。合成质量基准包含在离线回归中，也可单独运行以审查逐样本证据。

统一 `browser` 门还会在四份客户材料上传时停止本次独占 Redis，核对持久投递进入 `retry_wait`，再强制终止并重启三类 worker；必须以相同 delivery ID 完成后续原件、检索和报告链。它证明排队上传期间的队列中断与进程重启，不冒称在每个 I/O 中点都杀进程。独立 `candidate_ops_check.py` 在正常态和中断态读取实际容器、数据库与 `/api/readyz`，中断必须产生 `ALERT`。该浏览器场景不附对象对账计划，监控结果中的 objects 保持 `NOT_TESTED`；完整对象检查另见 [运维入口](../deploy/analysis-report/CANDIDATE_OPERATIONS.md)。新版本验证当前检索读新源且旧冻结引用/原件不变；含未重新计算公式的 Excel 必须明确 partial、零证据、问答不引用且新报告生成被阻止。

顶层 `report.json` 保存执行前后工作树指纹（包括未提交和未忽略的新文件）、实际进程退出码、完整日志 SHA 和逐项计数。退出码 0 仅表示该模式在源码稳定时通过；1 表示失败、错误、不完整或源码变化；2 表示未测。不同模式回执只有工作树指纹相同才属于同一候选。任何本地通过都不等同 `HUMAN_ACCEPTED`、CI 已执行或已经部署。

GitHub workflow 配置 offline/integration/restore/browser 四个 job，失败仍上传证据。真实材料不放进公共 CI；真实质量门由有授权的操作人以指定输入运行。配置存在不能作为 CI 通过证据，远端运行结果必须单独获取。
