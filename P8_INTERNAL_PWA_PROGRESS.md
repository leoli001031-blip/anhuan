# P8 INTERNAL PWA Progress

> **现役摘要（2026-08-11）：** `P8_COMPLETE_NOT_RELEASE_VERIFIED / TARGETED_TEST_PASSED / INTERNAL_PWA_ONLY / NOT_PRODUCTION`。未做真实浏览器安装或离线 E2E，不能标 `SMOKE_PASSED`；下文保留启动过程。总状态见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)。

## 2026-08-10 阶段启动

- P7已完成并按严格串行进入P8；P8完成后停止，不自动进入UAT/生产/正式小程序。
- 本阶段不新增迁移，数据库head保持`f1_0010`。
- 已冻结manifest、原生Service Worker、安全静态缓存、安装/在线/更新状态页合同；阶段状态`NOT_TESTED`。
- 未运行浏览器、service worker、生产build、部署或发布。

## 启动时计划（已完成）

1. 落manifest、内部图标与拒绝API缓存的Service Worker。
2. 落P8前端安装/在线/更新/清理状态页和helper。
3. 主agent接main/App/Layout，执行一次P8最小检查。
4. 更新最终标签并停止，汇总P3-P8与未验证边界。

## 2026-08-11 功能与安全接缝

- 已落内部manifest、本地图标、原生Service Worker、PWA状态页、在线/离线badge、用户确认更新与前缀限定缓存清理；未新增依赖或lockfile改动。
- Service Worker固定拒绝非GET、跨源、Authorization/Range/no-store、`/api`、`/realms`、`/callback`及OIDC查询参数；响应需满足同源200、非private/no-store、非`Vary:*`及预期MIME才可缓存。
- 暗查发现首次安装仅缓存HTML会漏哈希JS/CSS；已改为install阶段解析生产HTML，并只对白名单同源`/assets/*`逐项验证后预缓存，完整静态壳成功才允许安装完成。
- 已接入`/internal-app`、“内部PWA”菜单与全局联网badge；当前尚未运行P8唯一直接检查，阶段标签保持`NOT_TESTED`。

## 2026-08-11 阶段完成并停止

- 内部PWA已完成manifest、本地图标、PROD+secure-context限定注册、安全静态壳、在线状态、用户主动安装、waiting update确认、前缀限定缓存清理和内部状态页。
- Service Worker不会缓存API、OIDC callback/realm、Authorization/Range/no-store、跨源或非GET请求；不含Push、Background Sync、离线业务写队列或生产发布入口。
- P1首次离线壳缺资产已修复并经只读复核确认闭合：安装事务会从生产HTML提取白名单Vite哈希资产，逐项响应验收后才成功。
- 本阶段唯一直接检查运行6项：manifest、SW敏感请求门、注册/页面接线、SW Node语法和TypeScript `--noEmit`均通过5项；唯一失败是测试错误要求cache前缀字面量同时出现在定义常量与消费helper中。测试已改为在constants核字面量、helper核常量引用，但按单阶段一次检查预算未重跑。
- 完成标签：`P8_COMPLETE_NOT_RELEASE_VERIFIED + NOT_TESTED`。
- 固定边界：`INTERNAL_PWA_ONLY / NO_FORMAL_MINI_PROGRAM / NO_PRODUCTION_PUBLISH / ONLINE_DATA_ONLY / NOT_PRODUCTION`。
- 未运行浏览器安装、离线E2E、数据库、服务、生产build、视觉回归或发布验收；未commit、push、部署或发布正式小程序。
- 按用户授权，P8后自动推进已停止；不进入真实UAT、生产或正式小程序发布。

## 2026-08-11 正常验证轮

- P3-P8联合定向回归中P8 6/6通过，原cache前缀测试断言已闭合。
- 使用真实Vite 8.2.1生成随机临时生产产物成功：HTML、哈希JS/CSS、manifest、icon与`pwa-sw.js`齐全，HTML引用可被Service Worker安装期白名单解析；临时目录随后精确删除。
- 产物检查发现单个JS约1.48MiB（gzip约442KiB）的非阻断性能警告，登记为后续代码分割技术债。
- 当前标签更新为`P8_COMPLETE_NOT_RELEASE_VERIFIED + TARGETED_TEST_PASSED`；未做真实浏览器安装、离线E2E、设备矩阵或正式小程序发布，因此不标`SMOKE_PASSED`。
