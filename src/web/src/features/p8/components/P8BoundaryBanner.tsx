import { Alert } from "antd";

export default function P8BoundaryBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      message="INTERNAL_PWA_ONLY / NO_FORMAL_MINI_PROGRAM / NO_PRODUCTION_PUBLISH / ONLINE_DATA_ONLY / NOT_PRODUCTION"
      description="离线只提供静态应用壳；业务数据必须联网并继续走现有 OIDC 与 API，不缓存租户或用户数据。"
      style={{ marginBottom: 20 }}
    />
  );
}
