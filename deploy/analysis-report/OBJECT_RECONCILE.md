# 材料对象对账与修复

当前入口是一次性运维命令，不给 API、摄取、索引或报告 worker 增加管理员凭据。读取当前候选数据库与三个材料桶，输出只读计划；`apply` 只执行该计划中的修复动作，并逐项重新核对目标、源身份、任务状态、对象 SHA、ETag 和修改时间。

运行环境沿用候选的 `F1_LOCAL_ENGINEERING=1`、`F1_PG_HOST`、`F1_PG_PORT`、`F1_PG_DATABASE`、`MINIO_ENDPOINT`。运维专用 `F1_SECRETS_DIR` 须为 0700 目录，其中 `f1_bootstrap_dsn`、`minio_root_user`、`minio_root_password` 为当前用户拥有的 0600 文件。补齐上传数据库登记还需要同一环境的 `f1_api_password` 与当前管理员 `--actor-sub`；缺少有效管理员时该项保持 `BLOCKED`。这些文件不挂到业务 worker。

在仓库根目录执行，输出使用新的绝对路径，不覆盖已有回执：

```sh
PYTHONPATH=src:. python infra/f1/analysis_report_reconcile.py plan --output /absolute/path/plan.json
PYTHONPATH=src:. python infra/f1/analysis_report_reconcile.py apply --plan /absolute/path/plan.json --issue-id REVIEWED_ISSUE_ID --actor-sub CURRENT_MANAGER_SUB --output /absolute/path/apply.json
```

`--issue-id` 可以重复；省略时执行计划中所有可修复项。计划绑定数据库集群、数据库名、精确迁移 head 和存储端点/账号身份，有效期 24 小时；恢复到新集群或升级 head 后重新生成。计划哈希用于检测内容损坏，实际授权仍来自运维凭据、当前数据库状态以及补登记时的管理员权限。

| 发现 | 修复策略 |
| --- | --- |
| 数据库已释放、释放副本缺失 | 持源行锁，从已核验的隔离原件复制；读回校验 SHA/大小，保留所有业务状态 |
| 隔离原件缺失、释放副本完整 | 从已核验副本恢复；原 ETag 不一致则停止并报告，不能把字节恢复冒充完整身份恢复 |
| 预览 manifest 或 unit 缺失 | 用已核验原件重建，只有与数据库保存的预览 SHA 完全一致才补写；兼容旧固定 manifest 和新内容寻址 |
| 原件已写入、数据库仍 reserved/write_failed | 用当前管理员调用原有事务入口，补登记与单一持久投递/审计一起提交；不替代扫描或释放审批 |
| 超过 24 小时的无引用对象 | 重新核对所有源引用或当前预览引用并持必要数据库锁，确认对象快照未变后删除；活动任务、无法证明引用关系的前缀和新对象保留 |
| 源损坏、两份原件都缺失、预览重建 SHA 不符 | `inspect`，保留现状并使用有效备份或新版本处理，不覆盖现有可疑对象 |
| 已有释放副本但释放事务未提交 | `retain`，副本本身不是释放批准；由当前业务角色重试释放流程 |

每次修复先预留 0600 回执，并在外部修改前将动作 ID 写入、刷新和 fsync 到同目录 `.journal.jsonl`。中断后保留原计划和 journal，用新的输出路径重放；已成功的动作返回 `ALREADY_REPAIRED`，不会重新打开投递或覆盖当前预览。状态 `APPLIED` 只表示所选动作执行结果；`APPLIED_WITH_REMAINDERS`、`BLOCKED`、`NOT_SELECTED` 与 `remaining_inspection` 必须保留。再执行 `plan` 才能查看当前剩余问题。

单次最多盘点 10,000 个对象、读取 1 GiB 内容，单原件最多 50 MiB；超过限制、对象读取不稳定或目标不符会失败，不输出健康结论。清理会在短事务中锁定相关源行；完全无源引用的对象删除会短暂持有源表 SHARE 锁，锁等待超过 3 秒时该项停止，避免无限阻塞业务。与拥有同样管理员存储凭据的外部写入者之间没有分布式事务；管理员并发修改对象应停止后重新盘点。

本地实库/真实 MinIO 验证覆盖缺失副本和预览、损坏拒绝、迟到上传、成员停用、同事务登记回滚、引用并发变化、未知成功与实际 CLI 进程 SIGKILL 后重放。24 小时保留期通过可控时钟验证；不宣称测试进程实际等待了 24 小时。真实候选全栈、目标服务器和当前 head 备份恢复另行验收。
