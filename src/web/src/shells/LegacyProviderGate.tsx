// 旧工程界面门：按会话角色和路径限制 Layout 的业务入口。
// 加载/错误时不渲染 Layout；拒绝访问时返回对应身份首页。
// 这是前端体验门，不是后端安全边界。
import { Spin } from "antd";
import { Navigate, useLocation } from "react-router-dom";
import { useSessionAccess } from "../adapters";
import { canAccessLegacyProvider, canAccessLegacyPath, homePathFor } from "../adapters/SessionAccess";
import ErrorState from "../components/ErrorState";
import Layout from "../pages/Layout";

export default function LegacyProviderGate() {
  const location = useLocation();
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
  if (!canAccessLegacyProvider(session) || !canAccessLegacyPath(session, location.pathname)) {
    return <Navigate to={homePathFor(session.product_role)} replace />;
  }
  return <Layout />;
}
