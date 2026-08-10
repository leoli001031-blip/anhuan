# P8 INTERNAL PWA / NO FORMAL MINI PROGRAM 任务书

> **已完成的历史执行合同。** P8 当前为 `P8_COMPLETE_NOT_RELEASE_VERIFIED / TARGETED_TEST_PASSED / INTERNAL_PWA_ONLY / NOT_PRODUCTION`；未做浏览器离线 E2E。现役总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 状态与边界

- P7已收口为`P7_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`；P8为当前队列最后阶段，完成后停止。
- P8不新增数据库表或Alembic迁移；线性head保持`f1_0010`，避免为纯前端壳制造无意义迁移。
- P8只做内部可安装PWA壳、联网状态、更新提示、安全静态缓存与安装说明；不缓存API/租户数据、不做background sync/push、不发布正式小程序或任何商店包。
- 固定边界：`INTERNAL_PWA_ONLY / NO_FORMAL_MINI_PROGRAM / NO_PRODUCTION_PUBLISH / ONLINE_DATA_ONLY / NOT_PRODUCTION`。
- 不恢复F1.1.1发布验收；不跑全仓/E2E/coverage/benchmark/生产build/视觉回归；不commit、不push、不部署。

## 产品闭环

浏览器打开内部平台 → 识别PWA/standalone能力 → 用户主动安装 → 首页显示在线/离线与更新状态 → 静态应用壳可离线打开 → 业务数据必须重新联网并走现有OIDC/API → 用户可刷新更新或清除本应用静态缓存。

## Task 0：合同与文件地界

- 主agent单写任务文档、`public/manifest.webmanifest`、`public/pwa-sw.js`、`main.tsx`、App/Layout和测试。
- Frontend子任务只新增`src/web/src/features/p8/**`，实现状态页、安装控制、在线状态组件和注册helper；不碰public/main/App/Layout/package/lock。
- 不新增npm依赖，不改lockfile；原生Service Worker与Web App Manifest实现。
- Service Worker只处理同源GET静态资源/导航壳，固定拒绝`/api/`、OIDC callback、Authorization请求和非同源请求；不缓存响应正文型API、用户信息、通知、上传或报告。

## Wave 1：Manifest与内部安装

- manifest固定`name/short_name/start_url=/internal-app/scope=/display=standalone/theme/background/lang`。
- 图标为仓内无客户信息的本地静态图标；不引用外部URL。
- `/internal-app`显示安装能力、是否standalone、浏览器不支持时的人工说明；安装必须由用户点击，不自动弹窗。

## Wave 2：安全Service Worker

- 安装先读取并验证根HTML，再解析其中同源、无query、白名单`/assets/*`哈希资源；manifest、图标和全部引用逐项通过状态/MIME/cache-control门后才完成静态壳预缓存。运行时仍只缓存同源script/style/image/font静态GET。
- `/api/`、`/callback`、带Authorization、非GET、非同源一律直接network且绝不cache。
- navigation使用network-first并以静态根壳兜底；业务数据离线时明确不可用，不伪造旧数据。
- activate仅删除本应用固定前缀的旧cache，不清浏览器其他数据。

## Wave 3：在线状态、更新与清理

- Layout显示在线/离线badge；PWA状态页显示service-worker控制、安装、更新状态和固定边界。
- 检测到waiting worker时提供用户点击“应用更新”；不后台强制刷新正在填写的表单。
- “清除离线壳缓存”仅删除本应用cache前缀并重新加载；不清cookie/localStorage/OIDC或其他站点数据。

## Wave 4：集中接缝与停止

- App接`/internal-app`，Layout接“内部PWA”；现有根入口和业务路由保持不变。
- 仅修启动/编译、主链阻断、数据损坏、明确跨租户越权；其余登记BLOCKED。
- P8完成后停止并汇总P3-P8；不进入真实UAT、生产或正式小程序发布。

## 验证与状态

- P8最多一个预计60秒内的直接相关检查，覆盖manifest/SW缓存边界、注册helper、页面接线与TypeScript `--noEmit`；不运行生产build或浏览器E2E。
- 未运行标`NOT_TESTED`；通过仅`TARGETED_TEST_PASSED`。完成标签`P8_COMPLETE_NOT_RELEASE_VERIFIED`，禁止`RELEASE_VERIFIED/PUBLISHED`。
