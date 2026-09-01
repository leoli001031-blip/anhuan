// 客户上下文头：面包屑 + 客户级业务 tab。
// 客户名来自会话内的客户对象；路由 :clientId 是客户上下文的唯一来源。
import type { ReactNode } from "react";
import { Breadcrumb, Spin, Tabs, Typography } from "antd";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ErrorState from "../../components/ErrorState";
import { useClient } from "./useClient";

const TABS = [
  { key: "overview", label: "总览", path: "" },
  { key: "materials", label: "资料", path: "/materials" },
  { key: "services", label: "服务事项", path: "/services" },
  { key: "calendar", label: "日历", path: "/calendar" },
  { key: "rectification", label: "整改", path: "/rectification" },
  { key: "reports", label: "报告", path: "/reports" },
];

function activeTab(pathname: string, clientId: string): string {
  const suffix = pathname.slice(`/console/clients/${clientId}`.length);
  if (suffix.startsWith("/materials")) return "materials";
  if (suffix.startsWith("/services")) return "services";
  if (suffix.startsWith("/calendar")) return "calendar";
  if (suffix.startsWith("/rectification")) return "rectification";
  if (suffix.startsWith("/reports")) return "reports";
  return "overview";
}

export default function ClientShell({
  clientId,
  children,
}: {
  clientId: string;
  children: ReactNode;
}) {
  const { client, contextId, error, reload } = useClient(clientId);
  const navigate = useNavigate();
  const location = useLocation();

  if (contextId !== clientId) {
    return <Spin style={{ display: "block", margin: "64px auto" }} />;
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!client) return <Spin style={{ display: "block", margin: "64px auto" }} />;

  return (
    <main className="console-page client-shell">
      <Breadcrumb
        style={{ marginBottom: 8 }}
        items={[
          { title: <Link to="/console/clients">客户企业</Link> },
          { title: client.name },
        ]}
      />
      <Typography.Title level={2} className="client-shell__title">
        {client.name}
      </Typography.Title>
      <Tabs
        activeKey={activeTab(location.pathname, clientId)}
        onChange={(key) => {
          const tab = TABS.find((t) => t.key === key);
          if (tab) navigate(`/console/clients/${clientId}${tab.path}`);
        }}
        items={TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      {children}
    </main>
  );
}
