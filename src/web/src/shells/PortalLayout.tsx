// 客户门户壳：导航只呈现已接入真实合同的客户能力。
// 窄屏（<768px）：品牌+退出一行、导航整行等分 tab，邮箱隐藏，无逐字断行。
// 无侧边栏、无企业切换器——客户身份完全由会话推导。
import { MenuOutlined } from "@ant-design/icons";
import { Button, Layout, Spin, Typography } from "antd";
import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useSessionAccess, isMockData } from "../adapters";
import { homePathFor } from "../adapters/SessionAccess";
import { useAuth } from "../auth/OidcProvider";
import ErrorState from "../components/ErrorState";
import MockBadge from "../components/MockBadge";

const navLinkClass = (active: boolean) =>
  active ? "portal-nav__link portal-nav__link--active" : "portal-nav__link";

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
    <Layout style={{ minHeight: "100vh", background: "var(--eco-page-bg)" }}>
      <Layout.Header
        className="portal-header"
        style={{
          display: "flex",
          alignItems: "center",
          columnGap: 24,
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <span className="portal-brand">
          <strong>A‑Eco</strong>
          <Typography.Text strong>企业门户</Typography.Text>
          <i aria-hidden="true" />
          <Typography.Text className="portal-brand__context">安环服务平台</Typography.Text>
        </span>
        <nav className="portal-nav">
          <NavLink to="/portal" end className={({ isActive }) => navLinkClass(isActive)}>
            总览
          </NavLink>
          <NavLink to="/portal/services" className={({ isActive }) => navLinkClass(isActive)}>
            服务事项
          </NavLink>
          <NavLink to="/portal/qa" className={({ isActive }) => navLinkClass(isActive)}>
            资料问答
          </NavLink>
          <NavLink to="/portal/reports" className={({ isActive }) => navLinkClass(isActive)}>
            分析报告
          </NavLink>
          <NavLink to="/portal/health" className={({ isActive }) => navLinkClass(isActive)}>
            健康度
          </NavLink>
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
            <NavLink to="/portal" end className={({ isActive }) => navLinkClass(isActive)}>总览</NavLink>
            <NavLink to="/portal/services" className={({ isActive }) => navLinkClass(isActive)}>服务事项</NavLink>
            <NavLink to="/portal/qa" className={({ isActive }) => navLinkClass(isActive)}>资料问答</NavLink>
            <NavLink to="/portal/reports" className={({ isActive }) => navLinkClass(isActive)}>分析报告</NavLink>
            <NavLink to="/portal/health" className={({ isActive }) => navLinkClass(isActive)}>健康度</NavLink>
            <button type="button" onClick={() => void logout()}>退出登录</button>
          </div>
        </details>
        <Typography.Text className="portal-mobile-enterprise">
          {enterpriseLabel}
        </Typography.Text>
      </Layout.Header>
      <Layout.Content style={{ background: "var(--eco-page-bg)" }}>
        <Outlet />
      </Layout.Content>
      <MockBadge />
    </Layout>
  );
}
