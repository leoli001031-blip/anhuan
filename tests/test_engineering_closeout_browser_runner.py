"""Static contract for the dependency-free engineering browser verifier."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "src/web/scripts/engineering-browser-verify.mjs"


class EngineeringBrowserRunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RUNNER.read_text(encoding="utf-8")

    def test_uses_node_and_native_cdp_without_new_packages(self) -> None:
        for module in ("node:child_process", "node:fs/promises", "node:os", "node:path"):
            self.assertIn(module, self.source)
        self.assertIn("new WebSocket(websocketUrl)", self.source)
        for forbidden in ("playwright", "puppeteer", "selenium", "chromedriver"):
            self.assertNotIn(forbidden, self.source.lower())
        for method in (
            "Target.createBrowserContext",
            "Target.createTarget",
            "Target.attachToTarget",
            "Target.disposeBrowserContext",
            "Browser.close",
        ):
            self.assertIn(method, self.source)

    def test_inputs_secrets_and_chrome_profile_fail_closed(self) -> None:
        self.assertIn("process.argv.slice(2)", self.source)
        self.assertIn('inputs[index] === "--headed"', self.source)
        self.assertIn('inputs[index] === "--pwa-update-control"', self.source)
        self.assertIn("PWA_UPDATE_CONTROL_DIRECTORY_INVALID", self.source)
        self.assertIn("(info.mode & 0o777) !== 0o700", self.source)
        self.assertIn("(before.mode & 0o777) !== 0o600", self.source)
        self.assertIn("constants.O_NOFOLLOW", self.source)
        for secret in (
            "oidc_admin_anhuan_local",
            "oidc_auditor",
            "oidc_tenant_a",
        ):
            self.assertIn(secret, self.source)
        for boundary in (
            "--headless=new",
            "--window-position=-10000,-10000",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "anhuan-engineering-browser-",
            "Target.disposeBrowserContext",
            "SIGTERM",
            "SIGKILL",
            "child.signalCode !== null",
            'if (!exited) fail("CHROME_PROCESS_CLEANUP_FAILED")',
        ):
            self.assertIn(boundary, self.source)

    def test_covers_real_oidc_pages_tenant_headers_and_non_2xx(self) -> None:
        for identity in ("admin@anhuan.local", 'username: "auditor"', 'username: "tenant-a"'):
            self.assertIn(identity, self.source)
        for route in (
            '"/service-cases"',
            '"/controlled-documents"',
            '"/dashboard"',
            '"/policies"',
            '"/quality"',
            '"/rehearsal"',
            '"/internal-app"',
        ):
            self.assertIn(route, self.source)
        self.assertIn('name.toLowerCase() === "x-enterprise-id"', self.source)
        self.assertIn("status < 200 || status >= 300", self.source)
        self.assertIn("TENANT_OLD_STATE_RETAINED", self.source)
        self.assertIn("Input.dispatchMouseEvent", self.source)
        self.assertIn("Input.dispatchKeyEvent", self.source)
        self.assertIn("TENANT_SWITCH_OPEN_FAILED", self.source)
        self.assertIn("admin_api_non_2xx", self.source)
        self.assertIn('route !== "/internal-app"', self.source)
        for reason in (
            "OIDC_CREDENTIALS_REJECTED",
            "OIDC_FORM_TARGET_INVALID",
            "OIDC_CALLBACK_FAILED",
            "OIDC_LOGIN_LOOP",
            "OIDC_KEYCLOAK_COOKIE_ERROR",
            "OIDC_KEYCLOAK_REDIRECT_ERROR",
            "OIDC_KEYCLOAK_CLIENT_ERROR",
            "OIDC_KEYCLOAK_INTERNAL_ERROR",
            "OIDC_KEYCLOAK_ERROR_PAGE",
            "OIDC_WORKBENCH_SHELL_MISSING",
            "OIDC_ROOT_REDIRECT_STALLED",
            "OIDC_REDIRECT_STALLED",
        ):
            self.assertIn(reason, self.source)
        for key in (
            "WORKBENCH",
            "CALENDAR",
            "NOTIFICATIONS",
            "SERVICE_CASES",
            "MY_TASKS",
            "FINDINGS",
            "RECTIFICATION",
            "REVIEWS",
            "CONTROLLED_DOCUMENTS",
            "DASHBOARD",
            "CRM",
            "REPORTS",
            "POLICIES",
            "POLICY_IMPACT",
            "QUALITY",
            "REHEARSAL",
        ):
            self.assertIn(f'"{key}"', self.source)
        self.assertIn("fail(`ADMIN_${key}_API_NON_2XX`)", self.source)
        self.assertIn("fail(`ADMIN_${key}_API_${statuses[0]}`)", self.source)
        self.assertIn("OIDC_CREDENTIAL_PREFLIGHT_REJECTED", self.source)
        self.assertIn("OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED", self.source)
        self.assertIn("OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID", self.source)
        self.assertIn("OIDC_MULTIPLE_CREDENTIALS_REJECTED", self.source)
        self.assertIn("OIDC_ADMIN_CREDENTIAL_REJECTED", self.source)
        self.assertIn("OIDC_AUDITOR_CREDENTIAL_REJECTED", self.source)
        self.assertIn("OIDC_TENANT_CREDENTIAL_REJECTED", self.source)
        self.assertIn("/protocol/openid-connect/token", self.source)

    def test_covers_consultant_and_enterprise_pages_with_protected_api_evidence(self) -> None:
        for contract in (
            'identityKey: "auditor"',
            'route: "/reviews"',
            'protectedApiPath: "/api/v1/findings"',
            'route: "/quality/disagreements"',
            'protectedApiPath: "/api/v1/automated-quality/disagreements"',
            'identityKey: "tenant"',
            'route: "/rectification"',
            'route: "/service-cases"',
            'protectedApiPath: "/api/v1/service-cases"',
            "waitForProtectedApi",
            "IDENTITY_TENANT_CONTEXT_MISSING",
            "ROLE_IDENTITY_CONTRACT_INVALID",
        ):
            self.assertIn(contract, self.source)
        for metric in (
            "consultant_pages_visited",
            "enterprise_pages_visited",
            "role_api_non_2xx",
        ):
            self.assertIn(metric, self.source)
        for reason in (
            "CONSULTANT_REVIEWS",
            "CONSULTANT_QUALITY_DISAGREEMENTS",
            "ENTERPRISE_RECTIFICATION",
            "ENTERPRISE_SERVICE_CASES",
        ):
            self.assertIn(reason, self.source)
        self.assertIn('.ant-layout-content h1, .ant-layout-content h2', self.source)

    def test_pwa_checks_registration_sensitive_caches_and_offline_shell(self) -> None:
        for token in (
            "navigator.serviceWorker.getRegistrations()",
            "navigator.serviceWorker.controller",
            "name.startsWith",
            '"/api", "/realms", "/callback"',
            'request.headers.has("Authorization")',
            "Network.emulateNetworkConditions",
            "Network.overrideNetworkState",
            'connectionType: "none"',
            'connectionType: "wifi"',
            "offlineDocumentFromServiceWorker",
            "PWA_OFFLINE_SHELL_MISSING",
            "PWA_OFFLINE_STATUS_MISSING",
            "pwa_sensitive_cache_entries",
            'cdp.call("PWA.install"',
            'cdp.call("PWA.launch"',
            'cdp.call("PWA.uninstall"',
            "PWA_INSTALL_CLEANUP_FAILED",
            "pwa_installations",
            "Page.getAppManifest",
            "Page.getInstallabilityErrors",
            "pwa_installability_errors",
            "PWA_INSTALLABILITY_ICON_INVALID",
            "PWA_INSTALLABILITY_WORKER_INVALID",
            "PWA_INSTALLABILITY_MANIFEST_INVALID",
            "PWA_MANIFEST_ID_INVALID",
            "PWA_WAITING_UPDATE_MISSING",
            "PWA_OLD_ACTIVE_NOT_PRESERVED",
            "PWA_UPDATE_CONTROLLER_CHANGE_MISSING",
            "PWA_UPDATE_ACTIVATION_INVALID",
            'data-testid="pwa-apply-update"',
            "Page.addScriptToEvaluateOnNewDocument",
            "pwa_waiting_updates",
            "pwa_controller_changes",
            "pwa_old_caches_removed",
            "pwa_sentinel_caches_preserved",
            "pwa_login_states_preserved",
            "PWA_OS_INSTALL_NOT_TESTED",
            "PWA_STAGE_BASELINE_UNEXPECTED",
            "PWA_STAGE_WAIT_B_UNEXPECTED",
            "PWA_STAGE_REQUEST_UNEXPECTED",
            "PWA_STAGE_WAITING_UNEXPECTED",
            "PWA_STAGE_OFFLINE_UNEXPECTED",
            "PWA_STAGE_CONFIRM_UNEXPECTED",
            "PWA_STAGE_ACTIVATION_UNEXPECTED",
            "preserveCurrentClient: true",
            'data-testid="pwa-offline-frame"',
            'data-testid="pwa-check-update"',
        ):
            self.assertIn(token, self.source)

    def test_output_is_aggregate_and_script_has_valid_node_syntax(self) -> None:
        self.assertIn("LOCAL_BROWSER_VERIFY_OK", self.source)
        self.assertIn("LOCAL_BROWSER_VERIFY_FAILED", self.source)
        self.assertNotIn("console.log", self.source)
        main = self.source.split("async function main()", 1)[1]
        self.assertLess(main.index("await cleanupChrome(runtime)"), main.index("LOCAL_BROWSER_VERIFY_OK"))
        self.assertEqual(self.source.count("process.stdout.write"), 1)
        self.assertEqual(self.source.count("process.stderr.write"), 1)
        syntax = subprocess.run(
            ["node", "--check", str(RUNNER)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(syntax.returncode, 0, "BROWSER_RUNNER_NODE_SYNTAX_FAILED")


if __name__ == "__main__":
    unittest.main()
