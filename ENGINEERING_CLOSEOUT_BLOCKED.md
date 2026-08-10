# Engineering Closeout Blocked

当前无阻断代码与本地工程收口的事项。最终门已通过，项目状态为 `INTERNAL_ENGINEERING_READY / NOT_PRODUCTION`。以下两项是明确延期边界，不得被误报为已验证或运行中。

## DEFERRED-PWA-OS-INSTALL-MANUAL

- 事实：真实浏览器已通过 manifest installability、Service Worker 注册/控制、离线静态壳和同源 A→B waiting update 用户确认链；当前 macOS 自动验收环境未执行 OS 级应用安装。
- 影响：`pwa_installations=0`，必须保持 `NOT_TESTED`；不得据 Service Worker、离线壳或 installability 检查宣称桌面应用已安装。该边界不否定已通过的 PWA 运行时链，也不改变 `INTERNAL_PWA_ONLY / NOT_PRODUCTION`。
- 后续解法：如内部团队确实需要桌面安装证据，在受信任的人工 Chrome 会话中单独完成安装、启动和卸载检查，并独立记录。
- 状态：`DEFERRED_MANUAL_ENVIRONMENT_GATE / NOT_BLOCKING_CODE_CLOSEOUT / NOT_TESTED`。

## DEFERRED-PDF-INSPECTOR-PATCHED-SUPPLY

- 事实：`pdf-inspector 0.2.6` 使用 `lopdf 0.41.0`，受 `RUSTSEC-2026-0187` 影响；该发布包不能进入 API、worker、镜像或任意上传主链。
- 影响：PDF Inspector 本轮只完成架构决策，运行时保持默认关闭；不会阻断现有 P3 `pypdf` 预览、工程收口或 `NOT_PRODUCTION` 本地使用。
- 后续解法：另开任务书，先取得并固定 patched build，完成供应链、进程外无网、ClamAV clean-only、跨租户、失败清理和人工草稿边界验收，再决定是否启用 shadow。
- 状态：`DEFERRED / NOT_BLOCKING_CURRENT_CLOSEOUT / ARCHITECTURE_CONSIDERED / RUNTIME_DISABLED / NOT_PRODUCTION`。
