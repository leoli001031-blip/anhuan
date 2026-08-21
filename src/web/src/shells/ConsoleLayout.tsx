// 甲方运营台壳：顶部品牌条 + 左侧 200px 细线边栏（文字导航，无图标堆砌）。
// 客户上下文唯一来源是路由 :clientId，由客户列表进入，无切换器。
import { Button, Layout, Menu, Spin, Typography } from "antd";
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

  if (loading) return <Spin fullscreen tip="正在加载" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!session || session.product_role !== "provider_admin") {
    return <Navigate to={session ? homePathFor(session.product_role) : "/login"} replace />;
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 24,
          borderBottom: "1px solid var(--eco-border)",
          background: "var(--eco-content-bg)",
        }}
      >
        <Typography.Text strong style={{ fontSize: 15 }}>
          安环运营台
        </Typography.Text>
        <div style={{ flex: 1 }} />
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {user?.profile.email ?? user?.profile.preferred_username ?? ""}
        </Typography.Text>
        <Button type="text" size="small" onClick={() => void logout()}>
          退出
        </Button>
      </Layout.Header>
      <Layout>
        <Layout.Sider
          width={200}
          style={{
            borderRight: "1px solid var(--eco-border)",
            background: "var(--eco-content-bg)",
            paddingTop: 8,
          }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey(location.pathname)]}
            items={NAV}
            onClick={({ key }) => navigate(key)}
            style={{ border: "none", background: "transparent" }}
          />
        </Layout.Sider>
        <Layout.Content
          style={{ background: "var(--eco-page-bg)", padding: "24px 32px 64px" }}
        >
          <Outlet />
        </Layout.Content>
      </Layout>
      <MockBadge />
    </Layout>
  );
}
