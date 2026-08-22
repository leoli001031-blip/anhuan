// 旧工程界面门：包住 `/` 路由树。仅 provider_admin 进入 Layout。
// 加载/错误时不渲染 Layout。client_user 直达 /portal/qa。
// 这是前端体验门，不是后端安全边界。
import { Spin } from "antd";
import { Navigate } from "react-router-dom";
import { useSessionAccess } from "../adapters";
import { canAccessLegacyProvider } from "../adapters/SessionAccess";
import ErrorState from "../components/ErrorState";
import Layout from "../pages/Layout";

export default function LegacyProviderGate() {
  const { session, loading, error, reload } = useSessionAccess();
  if (loading) {
    return <Spin fullscreen tip="正在确认访问权限" />;
  }
  if (error) {
    return <ErrorState error={error} onRetry={reload} />;
  }
  if (!session) {
    return <Navigate to="/login" replace />;
  }
  if (!canAccessLegacyProvider(session)) {
    return <Navigate to="/portal/qa" replace />;
  }
  return <Layout />;
}
