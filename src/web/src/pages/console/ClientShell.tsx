// 客户上下文头：面包屑 + 四个二级 tab（总览｜资料｜服务事项｜报告）。
// 客户名来自会话内的客户对象；路由 :clientId 是客户上下文的唯一来源。
import type { ReactNode } from "react";
import { Breadcrumb, Spin, Tabs, Typography } from "antd";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ErrorState from "../../components/ErrorState";
import { useClient } from "./useClient";

// 服务事项缺客户粒度合同（BLOCKED-2），不作为正式主 tab。
const TABS = [
  { key: "overview", label: "总览", path: "" },
  { key: "materials", label: "资料", path: "/materials" },
  { key: "reports", label: "报告", path: "/reports" },
];

function activeTab(pathname: string, clientId: string): string {
  const suffix = pathname.slice(`/console/clients/${clientId}`.length);
  if (suffix.startsWith("/materials")) return "materials";
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
  const { client, error, reload } = useClient(clientId);
  const navigate = useNavigate();
  const location = useLocation();

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!client) return <Spin style={{ display: "block", margin: "64px auto" }} />;

  return (
    <div style={{ maxWidth: 1100 }}>
      <Breadcrumb
        style={{ marginBottom: 8 }}
        items={[
          { title: <Link to="/console/clients">客户企业</Link> },
          { title: client.name },
        ]}
      />
      <Typography.Title level={4} style={{ marginTop: 0 }}>
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
    </div>
  );
}
