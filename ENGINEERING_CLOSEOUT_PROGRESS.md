# Engineering Closeout Progress

## 2026-08-11 任务0开工回执

- 目标：单人可启动、维护、恢复并用真实浏览器操作 P2–P8，最终仅 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。
- 顺序：安全分支 → localctl/独立栈 → 数据库/RLS/后端 → OIDC/前端/PWA → 备份恢复 → 最终工程门。
- 基线：fresh clone `origin/main@8d2e791`，单根提交、564 文件，tree=`2070ced3fce8b0763dd6c8a2419414b92a702be2`。
- 分支：`codex/engineering-closeout`；未引入旧 repair/PDF Probe 历史，未 push、未部署。
- 当前 F1 唯一源码 head 为 `f1_0010`。
- 当前 Compose 有 18 服务/9 卷但无 PostgreSQL；API/worker 依赖外部数据库，缺统一运行和恢复入口。
- 本轮实跑 P3–P8 为 58/58 OK；P2–P8 合计 137 项中 4 项因旧测试硬编码 `f1_0005` 失败。
- 前端 lint/build exit 0；保留 2 个 warning 和约 1.48 MiB 单包技术债，不作为首个运行底座阻断。
- 最大风险：从 F0D 空库引导角色/Schema 的失败原子性、真实 OIDC 浏览器链、ClamAV 冷启动和备份恢复身份边界。

