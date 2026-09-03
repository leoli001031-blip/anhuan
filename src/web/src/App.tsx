// 路由结构：
//   新双壳：客户门户 /portal/*、甲方运营台 /console/*（新导航只暴露这些入口）
//   旧工程界面：原路径全部保留兼容，由 LegacyProviderGate 包住（仅 provider_admin 进入 Layout）
//   login/callback/portal/console 不受该门包裹；角色门只控制体验，权限以后端为准。
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { Alert, ConfigProvider, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { antdTheme } from "./theme";
import { OidcProvider, useAuth } from "./auth/OidcProvider";
import { ApiProvider, useSessionAccess } from "./adapters";
import { homePathFor } from "./adapters/SessionAccess";
import ErrorState from "./components/ErrorState";
import Login from "./pages/Login";
import LegacyProviderGate from "./shells/LegacyProviderGate";
import ConsoleLayout from "./shells/ConsoleLayout";
const QaPage = lazy(() => import("./pages/portal/QaPage"));
const PortalServicesPage = lazy(() => import("./pages/portal/PortalServicesPage"));
const PortalHomePage = lazy(() => import("./pages/portal/PortalHomePage"));
const PortalReportListPage = lazy(() => import("./pages/portal/ReportListPage"));
const PortalReportDetailPage = lazy(() => import("./pages/portal/ReportDetailPage"));
const HealthScorePage = lazy(() => import("./pages/portal/HealthScorePage"));
const ClientsPage = lazy(() => import("./pages/console/ClientsPage"));
const ClientOverviewPage = lazy(() => import("./pages/console/ClientOverviewPage"));
const ClientMaterialsPage = lazy(() => import("./pages/console/ClientMaterialsPage"));
const ClientServicesPage = lazy(() => import("./pages/console/ClientServicesPage"));
const ClientServiceCalendarPage = lazy(() => import("./pages/console/ClientServiceCalendarPage"));
const ClientRectificationPage = lazy(() => import("./pages/console/ClientRectificationPage"));
const ClientReportsPage = lazy(() => import("./pages/console/ClientReportsPage"));
const ReportWorkbenchPage = lazy(() => import("./pages/console/ReportWorkbenchPage"));
const ExceptionsPage = lazy(() => import("./pages/console/ExceptionsPage"));
const SharedMaterialsPage = lazy(() => import("./pages/console/SharedMaterialsPage"));
import PortalLayout from "./shells/PortalLayout";
const EnterpriseList = lazy(() => import("./pages/EnterpriseList"));
const LegacyQAPage = lazy(() => import("./pages/QAPage"));
const AuditPage = lazy(() => import("./pages/AuditPage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));
const InvitePage = lazy(() => import("./pages/InvitePage"));
const ServiceCaseList = lazy(() => import("./pages/ServiceCaseList"));
const ServiceCaseCreate = lazy(() => import("./pages/ServiceCaseCreate"));
const ServiceCaseDetail = lazy(() => import("./pages/ServiceCaseDetail"));
const FindingWorkbenchPage = lazy(() => import("./pages/FindingWorkbenchPage"));
const FindingCreate = lazy(() => import("./pages/FindingCreate"));
const FindingDetail = lazy(() => import("./pages/FindingDetail"));
const WorkbenchPage = lazy(() => import("./pages/WorkbenchPage"));
const ServiceCalendarPage = lazy(() => import("./pages/ServiceCalendarPage"));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage"));
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
  const { isAuthenticated } = useAuth();
  const { session, loading, error, reload } = useSessionAccess();
  if (loading) return <Spin fullscreen tip="正在加载" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!session) {
    return isAuthenticated ? <Spin fullscreen tip="正在加载" /> : <Navigate to="/login" replace />;
  }
  return <Navigate to={homePathFor(session.product_role)} replace />;
}

function AppRoutes() {
  const { isAuthenticated } = useAuth();
  return (
    <Suspense fallback={<Spin style={{ display: "block", margin: "96px auto" }} />}>
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/" replace /> : <Login />} />
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
        {/* 客户门户：首页 / 服务事项 / 资料问答 / 分析报告 */}
        <Route index element={<PortalHomePage />} />
        <Route path="services" element={<PortalServicesPage />} />
        <Route path="qa" element={<QaPage />} />
        <Route path="reports" element={<PortalReportListPage />} />
        <Route path="reports/:reportId" element={<PortalReportDetailPage />} />
        <Route path="health" element={<HealthScorePage />} />
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
        <Route path="clients/:clientId" element={<ClientOverviewPage />} />
        <Route path="clients/:clientId/materials" element={<ClientMaterialsPage />} />
        <Route path="clients/:clientId/services" element={<ClientServicesPage />} />
        <Route path="clients/:clientId/calendar" element={<ClientServiceCalendarPage />} />
        <Route path="clients/:clientId/rectification" element={<ClientRectificationPage />} />
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
            <Outlet />
          </Protected>
        }
      >
        <Route index element={<RoleHome />} />
        <Route element={<LegacyProviderGate />}>
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
      </Route>
      <Route path="*" element={<Navigate to={isAuthenticated ? "/" : "/login"} replace />} />
    </Routes>
    </Suspense>
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
