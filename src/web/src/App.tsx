import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { UserManager } from "oidc-client-ts";
import { ConfigProvider } from "antd";
import { OidcProvider, useAuth } from "./auth/OidcProvider";
import { oidcConfig } from "./auth/oidcConfig";
import Login from "./pages/Login";
import Layout from "./pages/Layout";
import EnterpriseList from "./pages/EnterpriseList";
import QAPage from "./pages/QAPage";
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

const userManager = new UserManager(oidcConfig);

function Callback() {
  const navigate = useNavigate();
  useEffect(() => {
    userManager.signinRedirectCallback().then(() => navigate("/workbench"));
  }, [navigate]);
  return null;
}

function Protected({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  const { isAuthenticated } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/callback" element={<Callback />} />
      <Route
        path="/"
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route index element={<Navigate to="/workbench" replace />} />
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
        <Route path="qa" element={<QAPage />} />
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
    <ConfigProvider>
      <OidcProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </OidcProvider>
    </ConfigProvider>
  );
}
