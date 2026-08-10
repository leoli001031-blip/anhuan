# Engineering Closeout Taskbook

目标：把 P2–P8 本地原型收成一个人可启动、维护、恢复和用真实浏览器操作的工程。完成标签只能是：

```text
INTERNAL_ENGINEERING_READY
NOT_PRODUCTION
```

优先级：数据与租户安全 > 可恢复 > 真实链路 > 易用 > 速度。

## 固定边界

- 开发分支固定 `codex/engineering-closeout`，根提交固定 `8d2e791b019ede7f1c3b5e939258952503bf7b89`。
- 本地旧 `main`、`codex/f1-1-1-repair` 和 PDF Probe 只读；禁止合并、rebase、cherry-pick 或复制旧历史。
- F1 唯一 head 保持 `f1_0010`；不新增业务表，不修改 `f1_0001` 至 `f1_0010`。
- 不恢复 F1.1.1 formal、reverse、SBOM、clean rebuild 或 M4；不做全仓 discover、生产部署或正式小程序发布。
- 不清理共享数据库、对象、容器、卷或 secret；本轮只操作带当前 project-id 与 label 的资源。
- 所有 active secret 使用当前用户拥有的 regular 0600 文件，父目录 0700；不得写入仓库、环境默认值、argv、日志或浏览器存储。

## 交付顺序

1. `./scripts/localctl`：start、stop、health、migrate、seed、reset、backup、restore、verify。
2. 自带 PostgreSQL、Keycloak、MinIO、Redis、ClamAV、API、worker、dispatcher、web 的独立本地栈；不依赖固定端口、旧共享栈、用户 HOME、外部 provider 或 RAGFlow。
3. 空库到 `f0d_0006/f1_0010`、重复迁移、失败原子性、31 张 P2–P7 业务表 FORCE RLS、真实低权限角色与跨租户边界。
4. 同一环境完成 P2–P7 主链、幂等、重启恢复、对象/扫描故障、非法状态跳转和事务回滚。
5. 真实 Keycloak/OIDC 浏览器链覆盖管理员、顾问、企业角色及 P2–P8 页面；权限按钮只来自 `allowed_actions`。
6. PWA 首次安装离线静态壳、敏感请求拒缓存、用户确认更新和本应用前缀精确清理。
7. PostgreSQL 备份恢复、MinIO 对象身份检查、精确 reset、日志泄漏门和三份本地运维文档。
8. PDF Inspector 仅形成受控集成决策；当前 `0.2.6/lopdf 0.41` 不得进入 API 或任意上传主链。

## 完成门

- 从空状态依次完成 reset → start → migrate 两次 → seed → health → verify → 重启 → backup → reset → restore → health → 浏览器 E2E → stop。
- P2–P8 定向测试不少于 137 项，失败 0、跳过 0；唯一 F1 head 为 `f1_0010`。
- 31 张业务表全部 ENABLE + FORCE RLS；跨租户 API 泄漏、RLS 读写泄漏、事务缺口、对象假状态、缓存泄漏、secret/log 泄漏、共享资源变更和本轮残留全部为 0。
- 每项保留真实命令输出与故障反测的红→绿证据；只报告实际达到的状态。

