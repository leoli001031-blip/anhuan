# P8 INTERNAL PWA Blocked / 技术债

## 当前阻断

- 无需用户决策的当前阻断；状态`TARGETED_TEST_PASSED / NOT_RELEASE_VERIFIED`。

## 延后问题

| reason | 影响面 | 建议修复点 | 状态 |
| --- | --- | --- | --- |
| 不缓存API或租户数据 | 离线仅能打开静态壳，业务数据不可离线读写 | 如未来有明确数据分类与冲突策略，再设计加密离线存储 | ONLINE_DATA_ONLY |
| 无push/background sync | 不支持离线提交与系统推送 | 需真实通知权限、隐私与生产授权后另做 | DEFERRED |
| 未发布正式小程序或商店包 | 当前仅浏览器内部PWA | 正式小程序需独立产品、审核、域名与发布流程 | NO_FORMAL_MINI_PROGRAM |
| 未做浏览器安装/E2E/视觉回归 | 真实Vite生产产物已通过，但不能证明不同设备安装与离线表现 | 后续单独做浏览器矩阵，不扩大为正式小程序发布 | DEFERRED_BROWSER_SMOKE |
| 单个生产JS约1.48MiB | 首次加载和弱网体验可能偏慢，Vite报告chunk size warning | 后续按P3-P8页面做route-level lazy import；不影响当前静态壳正确性 | DEFERRED_PERFORMANCE |
