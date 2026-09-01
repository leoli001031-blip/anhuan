// 客户上下文头：面包屑 + 材料/报告 二级 tab。客户名来自会话内的客户对象。
import type { ReactNode } from "react";
import { Breadcrumb, Spin, Tabs, Typography } from "antd";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ErrorState from "../../components/ErrorState";
import { useClient } from "./useClient";

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
  const tab = location.pathname.endsWith("/reports") ? "reports" : "materials";

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
        activeKey={tab}
        onChange={(key) =>
          navigate(`/console/clients/${clientId}/${key === "reports" ? "reports" : "materials"}`)
        }
        items={[
          { key: "materials", label: "材料" },
          { key: "reports", label: "报告" },
        ]}
      />
      {children}
    </div>
  );
}
