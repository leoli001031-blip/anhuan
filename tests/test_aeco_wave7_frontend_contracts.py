"""Static A-Eco frontend request-path contracts. No Node dependencies required."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
P2_API = ROOT / "src/web/src/p2Api.ts"
PORTAL_SERVICES = ROOT / "src/web/src/pages/portal/PortalServicesPage.tsx"
CLIENT_SERVICES = ROOT / "src/web/src/pages/console/ClientServicesPage.tsx"
CLIENT_SHELL = ROOT / "src/web/src/pages/console/ClientShell.tsx"
APP = ROOT / "src/web/src/App.tsx"
QA_PAGE = ROOT / "src/web/src/pages/portal/QaPage.tsx"
HTTP_API = ROOT / "src/web/src/adapters/HttpAnalysisReportApi.ts"
REPORT_WORKBENCH = ROOT / "src/web/src/pages/console/ReportWorkbenchPage.tsx"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


class PortalServiceFrontendContracts(unittest.TestCase):
    def test_portal_service_page_only_calls_client_safe_endpoint(self) -> None:
        api = _source(P2_API)
        page = _source(PORTAL_SERVICES)
        request = _between(
            api,
            "export async function listPortalServiceCases(",
            "export async function listClientServiceCases(",
        )
        self.assertIn('api<unknown>("/v1/service-cases/portal"', request)
        self.assertIn("row.items.map(parsePortalServiceCase)", request)
        self.assertIn("row.allowed_actions.length !== 0", request)
        self.assertNotIn("normalizeCollection", request)

        parser = _between(
            api,
            "function parsePortalServiceCase(",
            "export async function listPortalServiceCases(",
        )
        self.assertIn("keys.length !== PORTAL_SERVICE_CASE_KEYS.size", parser)
        self.assertIn("!PORTAL_SERVICE_CASE_KEYS.has(key)", parser)

        self.assertIn("listPortalServiceCases(getAccessToken())", page)
        self.assertNotIn("listServiceCases(getAccessToken()", page)
        self.assertNotIn("isMockData", page)
        self.assertNotIn("MockAnalysisReportApi", page)
        self.assertNotIn("item.description", page)
        self.assertNotIn("item.assignments", page)
        self.assertNotIn("item.findings", page)
        self.assertNotIn("item.timeline", page)
        self.assertIn('item.assigned ? "已指派" : "待指派"', page)

    def test_portal_dto_is_the_exact_backend_safe_summary(self) -> None:
        api = _source(P2_API)
        interface = _between(
            api,
            "export interface PortalServiceCaseSummary {",
            "export interface PortalServiceCaseCollection",
        )
        fields = tuple(re.findall(r"^\s*([a-z_]+):", interface, re.MULTILINE))
        self.assertEqual(
            fields,
            (
                "id",
                "title",
                "service_type",
                "status",
                "planned_start_at",
                "planned_end_at",
                "assigned",
                "updated_at",
            ),
        )


class ProviderClientServiceFrontendContracts(unittest.TestCase):
    def test_provider_list_is_route_client_filtered_and_scope_checked(self) -> None:
        api = _source(P2_API)
        page = _source(CLIENT_SERVICES)
        request = _between(
            api,
            "export async function listClientServiceCases(",
            "export function getServiceCase(",
        )
        self.assertIn(
            "/v1/service-cases?client_account_id=${encodeURIComponent(clientAccountId)}",
            request,
        )
        self.assertIn("item.client_account_id !== clientAccountId", request)
        self.assertIn("SERVICE_CASE_CLIENT_SCOPE_MISMATCH", request)
        self.assertIn("listClientServiceCases(getAccessToken(), clientId)", page)

        shell = _source(CLIENT_SHELL)
        app = _source(APP)
        self.assertIn('{ key: "services", label: "服务事项", path: "/services" }', shell)
        self.assertIn('path="clients/:clientId/services"', app)
        self.assertIn("<ClientServicesPage />", app)

    def test_client_scoped_create_always_injects_and_checks_client_id(self) -> None:
        api = _source(P2_API)
        page = _source(CLIENT_SERVICES)
        create = _between(
            api,
            "export async function createClientServiceCase(",
            "export function updateServiceCase(",
        )
        self.assertIn("client_account_id: clientAccountId", create)
        self.assertIn("created.client_account_id !== clientAccountId", create)
        self.assertIn("createClientServiceCase(getAccessToken(), clientId, values)", page)
        self.assertIn('collection?.allowed_actions.includes("create")', page)
        self.assertIn("只读：当前账号无创建服务事项权限", page)
        self.assertNotIn("navigate(", page)
        self.assertNotIn("isMockData", page)


class QaFrontendContracts(unittest.TestCase):
    def test_http_qa_has_no_mock_or_synthetic_fallback(self) -> None:
        adapter = _source(HTTP_API)
        ask = _between(
            adapter,
            "async ask(rawQuestion: string): Promise<QaAnswer> {",
            "// —— 客户端 · 已发布报告",
        )
        self.assertIn('this.request<unknown>("/v1/material-qa"', ask)
        self.assertIn("const tenant = getTenantSnapshot()", ask)
        self.assertIn("this.qaTenantGeneration !== tenant.generation", ask)
        self.assertIn("this.qaRequestIds.clear()", ask)
        self.assertIn("`${tenant.enterpriseId}\\u0000${question}`", ask)
        self.assertIn("this.qaRequestIds.get(requestKey) ?? crypto.randomUUID()", ask)
        self.assertIn("this.qaRequestIds.set(requestKey, requestId)", ask)
        self.assertIn("if (!answer.inProgress) this.qaRequestIds.delete(requestKey)", ask)
        self.assertIn("body: { question, request_id: requestId }", ask)
        self.assertIn("const answer = parseQaAnswer(status, payload, requestId)", ask)
        self.assertNotIn("catch", ask)
        self.assertNotIn("Mock", ask)
        self.assertNotIn("fallback", ask.lower())
        self.assertNotIn("CLIENT_QA_AUTH_BINDING_PENDING", adapter)

        page = _source(QA_PAGE)
        self.assertIn("const result = await api.ask(trimmed)", page)
        self.assertIn('setPhase({ kind: "done", result, question: trimmed })', page)
        self.assertIn("ask(phase.question)", page)
        self.assertNotIn("<Button onClick={() => void ask(question)}>", page)
        self.assertNotIn("isMockData", page)
        self.assertNotIn("MockAnalysisReportApi", page)

    def test_version_switch_has_one_complete_local_reset_effect(self) -> None:
        page = _source(REPORT_WORKBENCH)
        self.assertEqual(page.count("}, [selectedId]);"), 1)
        reset = _between(
            page,
            "// 审核勾选只属于当前选中版本",
            "// 归属证明",
        )
        for state_reset in (
            "setDetail(null)",
            "setChecked(emptyReviewChecklist())",
            "setReturnOpen(false)",
            'setReturnComment("")',
        ):
            self.assertIn(state_reset, reset)

        self.assertIn(
            '`${session?.enterprise_id ?? "unbound"}:${clientId}:${reportId}`',
            page,
        )
        self.assertIn("const jobStorageKey = `ar-job:${storageScope}`", page)
        self.assertIn(
            "const requestStorageKey = `ar-generate-request:${storageScope}`",
            page,
        )
        route_reset = _between(
            page,
            "// 路由客户/报告变化",
            "// 审核勾选只属于当前选中版本",
        )
        self.assertIn("const workbenchEpoch = useRef(0)", page)
        self.assertIn("workbenchEpoch.current += 1", route_reset)
        polling = _between(
            page,
            "// 生成进度轮询",
            "const refresh = useCallback",
        )
        self.assertIn("const contextAtStart = workbenchEpoch.current", polling)
        self.assertEqual(
            polling.count("if (contextAtStart !== workbenchEpoch.current) return"),
            2,
        )
        failure = _between(
            polling,
            ".catch((error) => {",
            "        });\n    }, 2000);",
        )
        transient = _between(failure, "if (retryable) {", "// 旧任务已永久失效")
        permanent = failure.split("// 旧任务已永久失效", 1)[1]
        self.assertIn("error instanceof ApiError", failure)
        self.assertIn("error.retryable", failure)
        self.assertIn('error.code === "REQUEST_ABORTED"', failure)
        self.assertIn("setJobPollFailed(true)", transient)
        self.assertNotIn("removeItem", transient)
        self.assertIn("sessionStorage.removeItem(jobStorageKey)", permanent)
        self.assertIn("setJob(null)", permanent)
        self.assertNotIn("sessionStorage.removeItem(requestStorageKey)", permanent)


if __name__ == "__main__":
    unittest.main()
