// 客户门户壳：顶部白条，导航按可用能力呈现（HTTP 问答未开放时隐藏）。
// 窄屏（<768px）：品牌+退出一行、导航整行等分 tab，邮箱隐藏，无逐字断行。
// 无侧边栏、无企业切换器——客户身份完全由会话推导。
import type { CSSProperties } from "react";
import { MenuOutlined } from "@ant-design/icons";
import { Button, Layout, Spin, Typography } from "antd";
import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useSessionAccess, isMockData } from "../adapters";
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
  whiteSpace: "nowrap",
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

  const enterpriseLabel = isMockData
    ? "青川精密制造有限公司"
    : (user?.profile.name ?? user?.profile.preferred_username ?? "当前企业");

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--eco-content-bg)" }}>
      <Layout.Header
        className="portal-header"
        style={{
          display: "flex",
          alignItems: "center",
          columnGap: 24,
          borderBottom: "1px solid var(--eco-border)",
          background: "var(--eco-content-bg)",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <span className="portal-brand">
          <strong>A-Eco</strong>
          <Typography.Text strong>安环服务平台</Typography.Text>
          <i aria-hidden="true" />
          <Typography.Text className="portal-brand__context">企业服务门户</Typography.Text>
        </span>
        <nav className="portal-nav">
          <NavLink to="/portal" end style={({ isActive }) => navLinkStyle(isActive)}>
            首页
          </NavLink>
          <NavLink to="/portal/reports" style={({ isActive }) => navLinkStyle(isActive)}>
            分析报告
          </NavLink>
          {/* 问答保留兼容路由，但在正式客户导航与视觉预览中均不展示。 */}
        </nav>
        <Typography.Text className="portal-email">
          {enterpriseLabel}
        </Typography.Text>
        <Button className="portal-desktop-logout" type="text" size="small" onClick={() => void logout()}>
          退出
        </Button>
        <details className="portal-mobile-menu">
          <summary aria-label="打开导航"><MenuOutlined aria-hidden="true" /></summary>
          <div>
            <NavLink to="/portal" end>首页</NavLink>
            <NavLink to="/portal/reports">分析报告</NavLink>
            <button type="button" onClick={() => void logout()}>退出登录</button>
          </div>
        </details>
        <Typography.Text className="portal-mobile-enterprise">
          {enterpriseLabel}
        </Typography.Text>
      </Layout.Header>
      <Layout.Content style={{ background: "var(--eco-content-bg)" }}>
        <Outlet />
      </Layout.Content>
      <MockBadge />
    </Layout>
  );
}
