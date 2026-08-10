# 安环平台前端

React 19 + TypeScript 6 + Vite 8 + Ant Design 6 + oidc-client-ts。页面覆盖 P2-P8 的业务工作台、受控文档、驾驶舱/CRM/报告、政策工作流、合成质量、本地演练和内部 PWA。

项目仍为 `NOT_RELEASE_VERIFIED / NOT_PRODUCTION`；总状态见仓库根 [PROJECT_STATUS.md](../../PROJECT_STATUS.md)。

## 本地开发

```bash
cd src/web
npm ci
npm run dev
```

Vite 使用同源 `/api` 代理后端，并通过 OIDC 回调完成登录。账号凭据不在 realm 文件中；本地环境由 0600 secret 文件注入，禁止把凭据写入源码或文档。

## 直接检查

```bash
npm run lint
npm run build
```

只有用户明确要求“正常验证”时才运行直接相关检查。最近 P8 证据是 Vite 生产构建通过，并非浏览器安装/离线 E2E；单 JS 约 1.48 MiB 的构建警告仍是非阻断性能债。

## 内部 PWA 边界

- `/internal-app` 仅说明内部安装、在线/离线和更新状态。
- Service Worker 只在生产构建且安全上下文注册。
- `/api`、OIDC callback、授权请求和租户业务数据绝不缓存。
- 不提供 background sync、push、离线写入、正式小程序或生产发布。
