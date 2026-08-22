from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE = ROOT / "src" / "web" / "src"


class EngineeringCloseoutFrontendApiTests(unittest.TestCase):
    def test_base_client_never_exposes_arbitrary_error_response_text(self) -> None:
        source = (WEB_SOURCE / "api.ts").read_text(encoding="utf-8")
        self.assertIn("export class ApiError extends Error", source)
        self.assertIn('"NETWORK_ERROR"', source)
        self.assertIn('"REQUEST_ABORTED"', source)
        self.assertIn("response.status === 429 || response.status >= 500", source)
        self.assertNotIn("response.text(", source)
        self.assertNotIn("resp.text(", source)

    def test_api_helper_callers_use_paths_relative_to_api_prefix(self) -> None:
        invalid: list[str] = []
        pattern = re.compile(r"\bapi(?:<[^;()]+>)?\(\s*['\"](/api/[^'\"]*)")
        for path in sorted(WEB_SOURCE.rglob("*")):
            if path.suffix not in {".ts", ".tsx"} or not path.is_file():
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                invalid.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(invalid, [])

    def test_tenant_change_aborts_old_requests_and_remounts_outlet(self) -> None:
        import ast

        api_source = (WEB_SOURCE / "api.ts").read_text(encoding="utf-8")
        state_source = (WEB_SOURCE / "tenantState.ts").read_text(encoding="utf-8")
        adapters_source = (WEB_SOURCE / "adapters" / "index.tsx").read_text(encoding="utf-8")
        http_source = (WEB_SOURCE / "adapters" / "HttpAnalysisReportApi.ts").read_text(
            encoding="utf-8"
        )
        layout_source = (WEB_SOURCE / "pages" / "Layout.tsx").read_text(encoding="utf-8")
        fixture_path = ROOT / "infra" / "f1" / "analysis-reports" / "local_browser_fixture.py"
        fixture_source = fixture_path.read_text(encoding="utf-8")
        pgint_source = (ROOT / "infra" / "f1" / "analysis_report_postgres_integration.py").read_text(
            encoding="utf-8"
        )

        binder = state_source.split("export function bindTenantRequest", 1)[1].split(
            "export function assertTenantBound", 1
        )[0]
        self.assertNotIn("localStorage", binder)
        self.assertNotIn("getSelectedEnterprise", binder)
        self.assertIn("validatedEnterpriseId", binder)
        self.assertIn("TENANT_SNAPSHOT_UNREADY", binder)
        self.assertNotIn("getSelectedEnterprise", http_source)
        self.assertIn("tenantFetch", http_source)
        self.assertNotIn('headers["X-Enterprise-Id"]', http_source)

        for token in (
            "let tenantRequestController = new AbortController()",
            "let tenantRequestGeneration = 0",
            "function invalidateTenantContext()",
            "tenantRequestGeneration += 1",
            "previousController.abort()",
            "tenantRequestController = new AbortController()",
            'window.addEventListener("storage", handleNativeStorage)',
            "mergeAbortSignals(options?.signal, tenantRequestController.signal)",
            "generation !== tenantRequestGeneration",
            "window.dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT))",
        ):
            self.assertIn(token, state_source)

        invalidate = state_source.split("export function invalidateTenantContext()", 1)[1].split(
            "function handleNativeStorage", 1
        )[0]
        self.assertLess(
            invalidate.index("tenantRequestGeneration += 1"),
            invalidate.index("previousController.abort()"),
        )
        self.assertLess(
            invalidate.index("previousController.abort()"),
            invalidate.index("window.dispatchEvent"),
        )

        storage = state_source.split("function handleNativeStorage", 1)[1].split(
            "if (typeof window", 1
        )[0]
        self.assertIn("event.key !== ENTERPRISE_KEY", storage)
        self.assertIn("event.oldValue === event.newValue", storage)
        self.assertIn("invalidateTenantContext()", storage)
        self.assertNotIn("localStorage", storage)

        setter = state_source.split("export function setSelectedEnterprise", 1)[1].split(
            "export function commitTenantSnapshot", 1
        )[0]
        self.assertIn("tenantSnapshotReady && validatedEnterpriseId === id", setter)
        self.assertNotIn("getSelectedEnterprise", setter)
        self.assertNotIn("current === id", setter)
        self.assertLess(setter.index("localStorage"), setter.index("invalidateTenantContext()"))

        self.assertIn("export async function tenantFetch", api_source)
        self.assertIn('headers["X-Enterprise-Id"] = bound.enterpriseId', api_source)
        self.assertIn("ENTERPRISE_HEADER_BYPASS_FORBIDDEN", api_source)

        self.assertIn('"/v1/users/me/enterprises"', adapters_source)
        self.assertIn("enterpriseId: null", adapters_source)
        self.assertIn("options.enterpriseId === null || path === MEMBERSHIP_PATH", api_source)
        self.assertGreaterEqual(adapters_source.count("born !== getTenantGeneration()"), 6)
        self.assertIn(".then(", adapters_source)
        self.assertIn(".catch(", adapters_source)
        self.assertIn(".finally(", adapters_source)

        self.assertIn("window.addEventListener(ENTERPRISE_CHANGED_EVENT", layout_source)
        self.assertIn("<Outlet key={tenantEpoch} />", layout_source)
        self.assertNotIn("window.dispatchEvent", layout_source)

        event_emitters: list[str] = []
        header_writers: list[str] = []
        for path in sorted(WEB_SOURCE.rglob("*")):
            if path.suffix not in {".ts", ".tsx"} or not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            if "dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT))" in source:
                event_emitters.append(path.relative_to(ROOT).as_posix())
            if '["X-Enterprise-Id"]' in source or '.set("X-Enterprise-Id"' in source:
                header_writers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(event_emitters, ["src/web/src/tenantState.ts"])
        self.assertEqual(header_writers, ["src/web/src/api.ts"])

        ast.parse(fixture_source)
        self.assertNotIn("DELETE FROM f1.enterprise_user", fixture_source)
        self.assertNotIn("SET role=", fixture_source)
        self.assertNotIn("UPDATE f1.enterprise_user", fixture_source)
        self.assertIn("INVITEE_SUB", fixture_source)
        self.assertIn('enterprise_id=local_seed.ENTERPRISE_B', fixture_source)
        self.assertIn("LOCAL_REPORT_FIXTURE_EMPLOYEE_MISMATCH", fixture_source)
        apply_body = fixture_source.split("def apply()", 1)[1].split("def main()", 1)[0]
        self.assertLess(
            apply_body.index("_preflight_target_identity"),
            apply_body.index("_ensure_profile"),
        )
        self.assertLess(
            apply_body.index("_preflight_membership_shape"),
            apply_body.index("_ensure_profile"),
        )
        self.assertNotIn("EMPLOYEE_SUB", apply_body)
        self.assertIn("INVITEE_SUB", apply_body)
        self.assertIn("def non_sensitive_identity_env", pgint_source)
        self.assertIn("identity.receipt", pgint_source)
        self.assertIn("LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME", pgint_source)


if __name__ == "__main__":
    unittest.main()
