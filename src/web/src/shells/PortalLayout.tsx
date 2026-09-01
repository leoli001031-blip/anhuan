// 客户门户壳：顶部 48px 白条，仅「智能问答 · 分析报告」两项导航。
// 无侧边栏、无企业切换器——客户身份完全由会话推导。
import type { CSSProperties } from "react";
import { Button, Layout, Spin, Typography } from "antd";
import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useSessionAccess } from "../adapters";
import { homePathFor } from "../adapters/SessionAccess";
import { useAuth } from "../auth/OidcProvider";
import ErrorState from "../components/ErrorState";
import MockBadge from "../components/MockBadge";

const navLinkStyle = (active: boolean): CSSProperties => ({
  color: active ? "var(--eco-primary)" : "var(--eco-text)",
  borderBottom: active ? "2px solid var(--eco-primary)" : "2px solid transparent",
  padding: "12px 2px",
  textDecoration: "none",
  fontSize: 14,
});

export default function PortalLayout() {
  const { user, logout } = useAuth();
  const { session, loading, error, reload } = useSessionAccess();

  if (loading) return <Spin fullscreen tip="正在加载" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  // 前端角色门仅控制体验；后端才是真正的安全边界。
  if (!session || session.product_role !== "client_user") {
    return <Navigate to={session ? homePathFor(session.product_role) : "/login"} replace />;
  }

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--eco-content-bg)" }}>
      <Layout.Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 32,
          borderBottom: "1px solid var(--eco-border)",
          background: "var(--eco-content-bg)",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <Typography.Text strong style={{ fontSize: 15 }}>
          安环智能助手
        </Typography.Text>
        <nav style={{ display: "flex", gap: 24, flex: 1 }}>
          <NavLink to="/portal/qa" style={({ isActive }) => navLinkStyle(isActive)}>
            智能问答
          </NavLink>
          <NavLink to="/portal/reports" style={({ isActive }) => navLinkStyle(isActive)}>
            分析报告
          </NavLink>
        </nav>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {user?.profile.email ?? user?.profile.preferred_username ?? ""}
        </Typography.Text>
        <Button type="text" size="small" onClick={() => void logout()}>
          退出
        </Button>
      </Layout.Header>
      <Layout.Content style={{ background: "var(--eco-content-bg)" }}>
        <Outlet />
      </Layout.Content>
      <MockBadge />
    </Layout>
  );
}
