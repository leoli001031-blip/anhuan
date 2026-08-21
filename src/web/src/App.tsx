// 路由结构：
//   新双壳：客户门户 /portal/*、甲方运营台 /console/*（新导航只暴露这些入口）
//   旧工程界面：原路径全部保留兼容（页面与 adapter 源码不删，新导航不展示其入口）
//   根路径按会话角色分流；角色门只控制体验，权限以后端为准。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Alert, ConfigProvider, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { antdTheme } from "./theme";
import { OidcProvider, useAuth } from "./auth/OidcProvider";
import { ApiProvider, useSessionAccess } from "./adapters";
import { homePathFor } from "./adapters/SessionAccess";
import ErrorState from "./components/ErrorState";
import Login from "./pages/Login";
import PortalLayout from "./shells/PortalLayout";
import ConsoleLayout from "./shells/ConsoleLayout";
import QaPage from "./pages/portal/QaPage";
import PortalReportListPage from "./pages/portal/ReportListPage";
import PortalReportDetailPage from "./pages/portal/ReportDetailPage";
import ClientsPage from "./pages/console/ClientsPage";
import ClientMaterialsPage from "./pages/console/ClientMaterialsPage";
import ClientReportsPage from "./pages/console/ClientReportsPage";
import ReportWorkbenchPage from "./pages/console/ReportWorkbenchPage";
import ExceptionsPage from "./pages/console/ExceptionsPage";
import SharedMaterialsPage from "./pages/console/SharedMaterialsPage";
// —— 以下为旧工程界面（保留兼容，不进新导航） ——
import Layout from "./pages/Layout";
import EnterpriseList from "./pages/EnterpriseList";
import LegacyQAPage from "./pages/QAPage";
import AuditPage from "./pages/AuditPage";
import AdminPage from "./pages/AdminPage";
import InvitePage from "./pages/InvitePage";
import ServiceCaseList from "./pages/ServiceCaseList";
import ServiceCaseCreate from "./pages/ServiceCaseCreate";
import ServiceCaseDetail from "./pages/ServiceCaseDetail";
import FindingWorkbenchPage from "./pages/FindingWorkbenchPage";
import FindingCreate from "./pages/FindingCreate";
import FindingDetail from "./pages/FindingDetail";
import WorkbenchPage from "./pages/WorkbenchPage";
import ServiceCalendarPage from "./pages/ServiceCalendarPage";
import NotificationsPage from "./pages/NotificationsPage";
import {
  DocumentDetailPage as ControlledDocumentDetailPage,
  DocumentLibraryPage as ControlledDocumentLibraryPage,
} from "./features/p3";
import {
  CrmAccountDetailPage,
  CrmAccountListPage,
  ReportDetailPage,
  ReportListPage,
  ReportVersionDetailPage,
  RoleDashboardPage,
} from "./features/p4";
import {
  PolicyImpactPage,
  PolicyDraftFromDocumentPage,
  PolicyLibraryPage,
  PolicySourceDetailPage,
  PolicyVersionDetailPage,
} from "./features/p5";
import {
  QualityDashboardPage,
  QualityDisagreementsPage,
  QualityRunDetailPage,
  QualitySuiteDetailPage,
} from "./features/p6";
import {
  LocalRehearsalDashboardPage,
  RehearsalPlanDetailPage,
  RehearsalRunDetailPage,
} from "./features/p7";
import { InternalPwaPage } from "./features/p8";

function Callback() {
  const navigate = useNavigate();
  const { completeSigninCallback } = useAuth();
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    completeSigninCallback()
      .then(() => navigate("/", { replace: true }))
      .catch(() => setError("登录回调失败，请重试。"));
  }, [completeSigninCallback, navigate]);
  if (error) return <Alert type="error" message="登录失败" description={error} />;
  return <Spin fullscreen tip="正在完成登录" />;
}

function Protected({ children }: { children: ReactNode }) {
  const { isAuthenticated, isInitializing } = useAuth();
  if (isInitializing) return <Spin fullscreen tip="正在检查登录状态" />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// 根路径按会话角色分流：客户 → 门户，服务商 → 运营台。
function RoleHome() {
  const { session, loading, error, reload } = useSessionAccess();
  if (loading) return <Spin fullscreen tip="正在加载" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!session) return <Navigate to="/login" replace />;
  return <Navigate to={homePathFor(session.product_role)} replace />;
}

function AppRoutes() {
  const { isAuthenticated } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/callback" element={<Callback />} />
      {/* 新 · 客户门户 */}
      <Route
        path="/portal"
        element={
          <Protected>
            <PortalLayout />
          </Protected>
        }
      >
        <Route index element={<Navigate to="/portal/qa" replace />} />
        <Route path="qa" element={<QaPage />} />
        <Route path="reports" element={<PortalReportListPage />} />
        <Route path="reports/:reportId" element={<PortalReportDetailPage />} />
      </Route>
      {/* 新 · 甲方运营台 */}
      <Route
        path="/console"
        element={
          <Protected>
            <ConsoleLayout />
          </Protected>
        }
      >
        <Route index element={<Navigate to="/console/clients" replace />} />
        <Route path="clients" element={<ClientsPage />} />
        <Route path="clients/:clientId/materials" element={<ClientMaterialsPage />} />
        <Route path="clients/:clientId/reports" element={<ClientReportsPage />} />
        <Route
          path="clients/:clientId/reports/:reportId"
          element={<ReportWorkbenchPage />}
        />
        <Route path="exceptions" element={<ExceptionsPage />} />
        <Route path="shared-materials" element={<SharedMaterialsPage />} />
      </Route>
      {/* 旧 · 工程界面（原路径保留兼容） */}
      <Route
        path="/"
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route index element={<RoleHome />} />
        <Route path="workbench" element={<WorkbenchPage />} />
        <Route path="calendar" element={<ServiceCalendarPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="dashboard" element={<RoleDashboardPage />} />
        <Route path="crm" element={<CrmAccountListPage />} />
        <Route path="crm/:accountId" element={<CrmAccountDetailPage />} />
        <Route path="reports" element={<ReportListPage />} />
        <Route path="reports/:reportId" element={<ReportDetailPage />} />
        <Route
          path="reports/:reportId/versions/:versionId"
          element={<ReportVersionDetailPage />}
        />
        <Route path="policies" element={<PolicyLibraryPage />} />
        <Route
          path="policies/import/:documentVersionId"
          element={<PolicyDraftFromDocumentPage />}
        />
        <Route
          path="policies/sources/:sourceId"
          element={<PolicySourceDetailPage />}
        />
        <Route
          path="policies/versions/:versionId"
          element={<PolicyVersionDetailPage />}
        />
        <Route path="policy-impact" element={<PolicyImpactPage />} />
        <Route path="quality" element={<QualityDashboardPage />} />
        <Route
          path="quality/suites/:suiteId"
          element={<QualitySuiteDetailPage />}
        />
        <Route
          path="quality/runs/:runId"
          element={<QualityRunDetailPage />}
        />
        <Route
          path="quality/disagreements"
          element={<QualityDisagreementsPage />}
        />
        <Route path="rehearsal" element={<LocalRehearsalDashboardPage />} />
        <Route
          path="rehearsal/plans/:planId"
          element={<RehearsalPlanDetailPage />}
        />
        <Route
          path="rehearsal/runs/:runId"
          element={<RehearsalRunDetailPage />}
        />
        <Route path="internal-app" element={<InternalPwaPage />} />
        <Route path="service-cases" element={<ServiceCaseList />} />
        <Route path="service-cases/new" element={<ServiceCaseCreate />} />
        <Route path="service-cases/:caseId" element={<ServiceCaseDetail />} />
        <Route path="my-tasks" element={<ServiceCaseList scope="mine" />} />
        <Route path="findings" element={<FindingWorkbenchPage scope="all" />} />
        <Route path="findings/new" element={<FindingCreate />} />
        <Route path="findings/:findingId" element={<FindingDetail />} />
        <Route
          path="rectification"
          element={<FindingWorkbenchPage scope="rectification" />}
        />
        <Route path="reviews" element={<FindingWorkbenchPage scope="review" />} />
        <Route path="enterprises" element={<EnterpriseList />} />
        <Route path="documents" element={<Navigate to="/controlled-documents" replace />} />
        <Route path="controlled-documents" element={<ControlledDocumentLibraryPage />} />
        <Route
          path="controlled-documents/:documentId"
          element={<ControlledDocumentDetailPage />}
        />
        <Route path="qa" element={<LegacyQAPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="invite" element={<InvitePage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to={isAuthenticated ? "/" : "/login"} replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <OidcProvider>
        <BrowserRouter>
          <ApiProvider>
            <AppRoutes />
          </ApiProvider>
        </BrowserRouter>
      </OidcProvider>
    </ConfigProvider>
  );
}
