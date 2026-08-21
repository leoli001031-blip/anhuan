import { useEffect, useState } from "react";
import { Layout as AntLayout, Menu, Button, Typography, Select } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/OidcProvider";
import {
  ENTERPRISE_CHANGED_EVENT,
  api,
  getSelectedEnterprise,
  setSelectedEnterprise,
} from "../api";
import type { Membership } from "../api";
import NotificationBell from "../components/NotificationBell";
import { OnlineOfflineBadge } from "../features/p8";

const { Header, Content } = AntLayout;

export default function Layout() {
  const { user, logout, getAccessToken } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [selected, setSelected] = useState<string | null>(getSelectedEnterprise());
  const [tenantEpoch, setTenantEpoch] = useState(0);

  useEffect(() => {
    const handleTenantChange = () => setTenantEpoch((current) => current + 1);
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, handleTenantChange);
    return () => window.removeEventListener(ENTERPRISE_CHANGED_EVENT, handleTenantChange);
  }, []);

  useEffect(() => {
    api<Membership[]>("/v1/users/me/enterprises", { token: getAccessToken() })
      .then((data) => {
        setMemberships(data);
        const stored = getSelectedEnterprise();
        const next = data.some((item) => item.enterprise_id === stored)
          ? stored
          : (data[0]?.enterprise_id ?? null);
        setSelected(next);
        if (next !== stored) {
          setSelectedEnterprise(next);
        }
      })
      .catch(() => setMemberships([]));
  }, [getAccessToken]);

  const items = [
    { key: "/workbench", label: "工作台" },
    { key: "/dashboard", label: "经营驾驶舱" },
    { key: "/calendar", label: "日历" },
    { key: "/service-cases", label: "服务任务" },
    { key: "/my-tasks", label: "我的任务" },
    { key: "/findings", label: "问题看板" },
    { key: "/rectification", label: "企业整改" },
    { key: "/reviews", label: "顾问复核" },
    { key: "/enterprises", label: "企业" },
    { key: "/controlled-documents", label: "受控文档" },
    { key: "/crm", label: "内部 CRM" },
    { key: "/reports", label: "业务报告" },
    { key: "/policies", label: "政策工作流" },
    { key: "/policy-impact", label: "政策影响" },
    { key: "/quality", label: "合成质量" },
    { key: "/rehearsal", label: "本地演练" },
    { key: "/internal-app", label: "内部 PWA" },
    { key: "/qa", label: "问答" },
    { key: "/audit", label: "审计" },
    { key: "/invite", label: "邀请" },
    { key: "/admin", label: "管理后台" },
  ];

  const selectedMenuKey = location.pathname.startsWith("/service-cases")
    ? "/service-cases"
    : location.pathname.startsWith("/crm")
      ? "/crm"
    : location.pathname.startsWith("/reports")
      ? "/reports"
    : location.pathname.startsWith("/policies")
      ? "/policies"
    : location.pathname.startsWith("/policy-impact")
      ? "/policy-impact"
    : location.pathname.startsWith("/quality")
      ? "/quality"
    : location.pathname.startsWith("/rehearsal")
      ? "/rehearsal"
    : location.pathname.startsWith("/internal-app")
      ? "/internal-app"
    : location.pathname.startsWith("/controlled-documents")
      ? "/controlled-documents"
    : location.pathname.startsWith("/findings")
      ? "/findings"
      : location.pathname;

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
          height: "auto",
          minHeight: 64,
          paddingBlock: 8,
        }}
      >
        <Typography.Text style={{ color: "#fff", fontSize: 16 }}>
          安环平台
        </Typography.Text>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selectedMenuKey]}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{ flex: "1 1 520px", minWidth: 280 }}
        />
        <Select
          placeholder="选择企业"
          style={{ width: 180 }}
          value={selected}
          onChange={(id) => {
            setSelected(id);
            setSelectedEnterprise(id);
          }}
          options={memberships.map((m) => ({
            value: m.enterprise_id,
            label: `${m.name} (${m.role})`,
          }))}
        />
        <NotificationBell />
        <OnlineOfflineBadge />
        <Typography.Text style={{ color: "#fff" }}>
          {user?.profile.email ?? user?.profile.preferred_username ?? ""}
        </Typography.Text>
        <Button onClick={() => logout()}>退出</Button>
      </Header>
      <Content style={{ padding: "clamp(12px, 3vw, 24px)", overflowX: "hidden" }}>
        <Outlet key={tenantEpoch} />
      </Content>
    </AntLayout>
  );
}
