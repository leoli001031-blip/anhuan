// 甲方运营台壳：顶部品牌条 + 左侧 200px 细线边栏（文字导航，无图标堆砌）。
// <1280px：固定侧栏收起，顶栏出现「菜单」按钮，导航改为抽屉。
// 客户上下文唯一来源是路由 :clientId，由客户列表进入，无切换器。
import { useState } from "react";
import { Button, Drawer, Layout, Menu, Spin, Typography } from "antd";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSessionAccess, isMockData } from "../adapters";
import { homePathFor } from "../adapters/SessionAccess";
import { useAuth } from "../auth/OidcProvider";
import ErrorState from "../components/ErrorState";
import MockBadge from "../components/MockBadge";

const NAV = [
  { key: "/console/clients", label: "客户企业" },
  { key: "/console/shared-materials", label: "共享材料" },
  ...(isMockData ? [{ key: "/console/exceptions", label: "异常中心" }] : []),
];

function selectedKey(pathname: string): string {
  if (pathname.startsWith("/console/shared-materials")) return "/console/shared-materials";
  if (pathname.startsWith("/console/exceptions")) return "/console/exceptions";
  return "/console/clients";
}

export default function ConsoleLayout() {
  const { user, logout } = useAuth();
  const { session, loading, error, reload } = useSessionAccess();
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);

  if (loading) return <Spin fullscreen tip="正在加载" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!session || session.product_role !== "provider_admin") {
    return <Navigate to={session ? homePathFor(session.product_role) : "/login"} replace />;
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        className="console-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          borderBottom: "1px solid var(--eco-border)",
          background: "var(--eco-content-bg)",
        }}
      >
        <Button
          type="text"
          size="small"
          className="console-menu-button"
          onClick={() => setNavOpen(true)}
        >
          菜单
        </Button>
        <Typography.Text strong className="console-brand">
          A‑Eco <span>安环运营台</span>
        </Typography.Text>
        <div style={{ flex: 1 }} />
        <Typography.Text type="secondary" className="console-email" style={{ fontSize: 13 }}>
          {user?.profile.email ?? user?.profile.preferred_username ?? ""}
        </Typography.Text>
        <Button type="text" size="small" onClick={() => void logout()}>
          退出
        </Button>
      </Layout.Header>
      <Layout>
        <Layout.Sider
          width={200}
          className="console-sider"
          style={{
            borderRight: "1px solid var(--eco-border)",
            background: "var(--eco-primary)",
            paddingTop: 8,
          }}
        >
          <Menu
            className="console-nav"
            mode="inline"
            selectedKeys={[selectedKey(location.pathname)]}
            items={NAV}
            onClick={({ key }) => navigate(key)}
            style={{ border: "none", background: "transparent" }}
          />
        </Layout.Sider>
        <Drawer
          placement="left"
          open={navOpen}
          onClose={() => setNavOpen(false)}
          width={220}
          title="安环运营台"
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey(location.pathname)]}
            items={NAV}
            onClick={({ key }) => {
              setNavOpen(false);
              navigate(key);
            }}
            style={{ border: "none", background: "transparent" }}
          />
        </Drawer>
        <Layout.Content
          className="console-content"
          style={{ background: "var(--eco-page-bg)", padding: "24px 32px 64px" }}
        >
          <Outlet />
        </Layout.Content>
      </Layout>
      <MockBadge />
    </Layout>
  );
}
