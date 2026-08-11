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
        self.assertIn('inputs[index] === "--install-pwa"', self.source)
        self.assertIn('inputs[index] === "--stage"', self.source)
        self.assertIn('inputs[index] === "--pwa-update-control"', self.source)
        self.assertIn('let stage = "all"', self.source)
        self.assertIn('new Set(["all", "business", "faults", "pwa-update", "pwa-os"])', self.source)
        self.assertIn("BROWSER_STAGE_INVALID", self.source)
        self.assertIn("BROWSER_STAGE_CONTROL_REQUIRED", self.source)
        self.assertIn("BROWSER_STAGE_OPTION_INVALID", self.source)
        self.assertIn("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY", self.source)
        self.assertIn("PWA_OS_CONTROL_REQUIRED", self.source)
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
            "headed || installPwa",
            "os.userInfo()",
            "HOME: systemHome ?? profile",
        ):
            self.assertIn(boundary, self.source)
        self.assertNotIn("os.homedir()", self.source)

    def test_stage_router_keeps_all_contract_and_runs_only_selected_lane(self) -> None:
        for tag in (
            'all: "LOCAL_BROWSER_VERIFY_OK"',
            'business: "LOCAL_BROWSER_BUSINESS_VERIFY_OK"',
            'faults: "LOCAL_BROWSER_FAULTS_VERIFY_OK"',
            '"pwa-update": "LOCAL_PWA_UPDATE_VERIFY_OK"',
        ):
            self.assertIn(tag, self.source)
        self.assertNotIn('"pwa-os": "LOCAL_PWA_OS_VERIFY_OK"', self.source)
        for function in (
            "async function executeAll",
            "async function executeBusiness",
            "async function executeFaults",
            "async function executePwaUpdate",
            "async function executePwaOs",
            "async function executeStage",
            "function preflightIdentitiesForStage",
        ):
            self.assertIn(function, self.source)

        all_stage = self.source.split("async function executeAll", 1)[1].split(
            "async function executeBusiness", 1
        )[0]
        for token in (
            "visitAdminPages",
            "verifyMinio503Recovery",
            "verifyClamdUnavailableRecovery",
            "verifyTenantSwitch",
            "verifyWaitingPwaUpdate",
            "verifyPwaInstallation",
            'stage: "all"',
            "pwa_apply_clicks",
        ):
            self.assertIn(token, all_stage)

        business = self.source.split("async function executeBusiness", 1)[1].split(
            "async function executeFaults", 1
        )[0]
        for token in (
            "visitAdminPages",
            "verifyTenantSwitch",
            "ROLE_PAGE_CONTRACTS",
            'stage: "business"',
            "admin_api_responses",
        ):
            self.assertIn(token, business)
        for forbidden in (
            "verifyMinio503Recovery",
            "verifyClamdUnavailableRecovery",
            "verifyWaitingPwaUpdate",
            "verifyPwaInstallation",
        ):
            self.assertNotIn(forbidden, business)

        faults = self.source.split("async function executeFaults", 1)[1].split(
            "async function executePwaUpdate", 1
        )[0]
        self.assertIn("verifyMinio503Recovery", faults)
        self.assertIn("verifyClamdUnavailableRecovery", faults)
        self.assertIn('stage: "faults"', faults)
        self.assertNotIn("verifyTenantSwitch", faults)
        self.assertNotIn("verifyWaitingPwaUpdate", faults)

        pwa_update = self.source.split("async function executePwaUpdate", 1)[1].split(
            "async function executePwaOs", 1
        )[0]
        for token in (
            "auditPwaCaches",
            "verifyWaitingPwaUpdate",
            'stage: "pwa-update"',
            "pwa_apply_clicks",
        ):
            self.assertIn(token, pwa_update)
        self.assertNotIn("verifyPwaInstallation", pwa_update)
        self.assertNotIn("verifyTenantSwitch", pwa_update)

        pwa_os = self.source.split("async function executePwaOs", 1)[1].split(
            "function preflightIdentitiesForStage", 1
        )[0]
        self.assertIn("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY", pwa_os)
        self.assertNotIn("verifyPwaInstallation", pwa_os)
        self.assertNotIn("runIdentity", pwa_os)
        self.assertIn('if (stage === "pwa-os") return []', self.source)

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
        self.assertIn("function requestHeader(headers, expectedName)", self.source)
        self.assertIn("name.toLowerCase() === expectedName", self.source)
        self.assertIn('requestHeader(headers, "x-enterprise-id")', self.source)
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

    def test_proves_allowed_actions_and_real_error_ui_without_faking_responses(self) -> None:
        for token in (
            "verifyActionVisibility",
            '"ADMIN_SERVICE_CASE_CREATE"',
            'reasonKey: "CONSULTANT_SERVICE_CASE_CREATE"',
            "VISIBLE_ACTION_MISSING",
            "HIDDEN_ACTION_VISIBLE",
            "role_allowed_action_ui_checks",
            "Local durability canary",
            "ACTIVE_ASSIGNMENT_EXISTS",
            "ASSIGNMENT_ILLEGAL_STATE_409_MISSING",
            "ASSIGNMENT_ILLEGAL_STATE_UI_MISSING",
            "illegal_state_409_ui_count",
            "CROSS_TENANT_DETAIL_404_MISSING",
            "CROSS_TENANT_DETAIL_404_UI_MISSING",
            "cross_tenant_404_ui_count",
            "expected_fault_api_non_2xx",
            "service_unavailable_503_ui_count:",
            "service_unavailable_503_ui_status:",
            "waitForApiStatus",
            "\u65e0\u6cd5\u6253\u5f00\u670d\u52a1\u4efb\u52a1",
            "\u91cd\u8bd5",
        ):
            self.assertIn(token, self.source)
        for forbidden in (
            "Fetch.fulfillRequest",
            "Network.setBlockedURLs",
            "mockFetch",
            "mockResponse",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn("status !== 200", self.source)
        self.assertIn("status !== 404", self.source)
        self.assertIn("status !== 409", self.source)
        self.assertIn("PWA_OS_INSTALL_NOT_TESTED", self.source)

    def test_minio_503_handshake_uses_real_dom_and_same_idempotency_key(self) -> None:
        for token in (
            'const MINIO_FAULT_READY_SIGNAL = "minio-fault-ready"',
            'const MINIO_STOPPED_SIGNAL = "minio-stopped"',
            'const MINIO_503_OBSERVED_SIGNAL = "minio-503-observed"',
            'const MINIO_RESTORED_SIGNAL = "minio-restored"',
            "RUNNER_CONTROL_SIGNALS",
            "ALL_CONTROL_SIGNALS",
            "constants.O_EXCL | constants.O_NOFOLLOW",
            "(info.mode & 0o777) !== 0o600",
            "DOM.getDocument",
            "DOM.querySelector",
            "DOM.setFileInputFiles",
            "engineering-minio-recovery-canary.pdf",
            "syntheticPdfBytes",
            "anhuan-minio-fault-${probe}",
            "MINIO_FAULT_TENANT_A_REQUIRED",
            "MINIO_FAULT_CONTROL_SIGNAL_INVALID",
            "MINIO_FAULT_STOP_TIMEOUT",
            "MINIO_FAULT_503_API_MISSING",
            "MINIO_FAULT_503_API_INVALID",
            "MINIO_FAULT_503_RETRY_UI_MISSING",
            "MINIO_FAULT_RESTORE_TIMEOUT",
            "MINIO_FAULT_RETRY_API_MISSING",
            "MINIO_FAULT_RETRY_API_INVALID",
            "MINIO_FAULT_IDEMPOTENCY_MISMATCH",
            'unavailableEvents[0].status !== 503',
            'retryEvents[0].status !== 202',
            "uploadRequests[0].idempotencyKey !== uploadRequests[1].idempotencyKey",
            'status: "PASSED"',
        ):
            self.assertIn(token, self.source)
        self.assertLess(
            self.source.index("MINIO_FAULT_READY_SIGNAL"),
            self.source.index("MINIO_STOPPED_SIGNAL"),
        )
        self.assertNotIn("Fetch.fulfillRequest", self.source)
        self.assertNotIn("Network.setBlockedURLs", self.source)
        self.assertNotIn("process.exit(130)", self.source)
        main = self.source.split("async function main()", 1)[1]
        self.assertIn("cleanupChrome(runtime)", main)
        self.assertLess(
            main.index("await cleanupChrome(runtime)"),
            main.index("process.removeListener"),
        )
        recovery = self.source.split(
            "async function verifyMinio503Recovery", 1
        )[1].split("async function waitForScannerUiState", 1)[0]
        self.assertIn("finally", recovery)
        self.assertIn("cleanupSyntheticPdfArtifact(artifact)", recovery)

    def test_clamd_unavailable_and_recovery_use_real_capabilities_ui(self) -> None:
        for token in (
            'const CLAMD_FAULT_READY_SIGNAL = "clamd-fault-ready"',
            'const CLAMD_STOPPED_SIGNAL = "clamd-stopped"',
            'const CLAMD_UNAVAILABLE_OBSERVED_SIGNAL = "clamd-unavailable-observed"',
            'const CLAMD_RESTORED_SIGNAL = "clamd-restored"',
            'const INGESTION_CAPABILITIES_PATH = "/api/v1/ingestion/capabilities"',
            "verifyClamdUnavailableRecovery",
            "refreshScannerCapabilities",
            "waitForScannerUiState",
            "CLAMD_FAULT_CONTROL_SIGNAL_INVALID",
            "CLAMD_FAULT_STOP_TIMEOUT",
            "CLAMD_FAULT_UNAVAILABLE_API_MISSING",
            "CLAMD_FAULT_UNAVAILABLE_API_INVALID",
            "CLAMD_FAULT_UNAVAILABLE_UI_MISSING",
            "CLAMD_FAULT_RESTORE_TIMEOUT",
            "CLAMD_FAULT_RECOVERY_API_MISSING",
            "CLAMD_FAULT_RECOVERY_API_INVALID",
            "CLAMD_FAULT_RECOVERY_UI_MISSING",
            "本地扫描不可用",
            "新文件会继续留在隔离区，不会降级放行。",
            "本地扫描可用",
            'events[0].status !== 200',
            "clamd_unavailable_ui_count",
            "clamd_recovery_ui_count",
            "expectedNon2xx: 0",
        ):
            self.assertIn(token, self.source)
        execute = self.source.split("async function execute", 1)[1]
        self.assertLess(
            execute.index("verifyMinio503Recovery"),
            execute.index("verifyClamdUnavailableRecovery"),
        )
        self.assertNotIn("Fetch.fulfillRequest", self.source)
        self.assertNotIn("Network.setBlockedURLs", self.source)

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
            "pwa_installations",
            'const PWA_OS_OFFLINE_READY_SIGNAL = "pwa-os-offline-ready"',
            'const PWA_OS_WEB_STOPPED_SIGNAL = "pwa-os-web-stopped"',
            'const PWA_OS_OFFLINE_OBSERVED_SIGNAL = "pwa-os-offline-observed"',
            'const PWA_OS_WEB_RESTORED_SIGNAL = "pwa-os-web-restored"',
            "PWA_OS_SHIM_MISSING",
            "PWA_OS_SHIM_IDENTITY_INVALID",
            "CrAppModeUserDataDir",
            "CrAppModeShortcutURL",
            "CrAppModeShortcutID",
            "CFBundleIdentifier",
            "Chrome Apps.localized",
            "osOnlineLaunches",
            "osOfflineReopens",
            "osShimsCreated",
            "osUninstallations",
            "osUninstallProbe",
            "osShimResiduals",
            "pwa_os_online_launches",
            "pwa_os_offline_reopens",
            "pwa_os_shims_created",
            "pwa_os_uninstallations",
            "pwa_os_uninstall_probe",
            "pwa_os_shim_residuals",
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
            "PWA_UPDATE_APPLY_CAPTURE_FAILED",
            "PWA_UPDATE_APPLY_CLICK_NOT_CAPTURED",
            "PWA_UPDATE_APPLY_NOT_TRIGGERED",
            "PWA_UPDATE_CONTROLLER_PROBE_MISSED",
            "PWA_UPDATE_CONTROLLER_CHANGE_COUNT_INVALID",
            "PWA_UPDATE_ACTIVATION_INVALID",
            'data-testid="pwa-apply-update"',
            "Page.addScriptToEvaluateOnNewDocument",
            "pwa_waiting_updates",
            "pwa_controller_changes",
            "pwa_old_caches_removed",
            "pwa_sentinel_caches_preserved",
            "pwa_login_states_preserved",
            "PWA_OS_INSTALL_NOT_TESTED",
            "PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY",
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
            "PWA_APPLY_CLICK_KEY",
            "event.isTrusted",
            'document.addEventListener("click"',
            "pwa_apply_clicks",
        ):
            self.assertIn(token, self.source)
        update = self.source.split("async function verifyWaitingPwaUpdate", 1)[1].split(
            "async function verifyCredentialAtIdentityProvider", 1
        )[0]
        self.assertIn("Input.dispatchMouseEvent", self.source)
        self.assertNotIn(".click()", update)
        capture_index = update.index("__anhuanEngineeringPwaApplyClickCapture")
        apply_click_index = update.index("await page.clickElement", capture_index)
        self.assertLess(
            capture_index,
            apply_click_index,
        )
        self.assertLess(
            update.index("PWA_UPDATE_APPLY_CLICK_NOT_CAPTURED"),
            update.index("PWA_UPDATE_CONTROLLER_CHANGE_MISSING"),
        )

    def test_output_is_aggregate_and_script_has_valid_node_syntax(self) -> None:
        self.assertIn("LOCAL_BROWSER_VERIFY_OK", self.source)
        self.assertIn("LOCAL_BROWSER_VERIFY_FAILED", self.source)
        self.assertNotIn("console.log", self.source)
        main = self.source.split("async function main()", 1)[1]
        self.assertLess(main.index("await cleanupChrome(runtime)"), main.index("process.stdout.write"))
        self.assertLess(
            main.index("if (primaryError) throw primaryError"),
            main.index("if (cleanupError) throw cleanupError"),
        )
        minio = self.source.split(
            "async function verifyMinio503Recovery", 1
        )[1].split("async function waitForScannerUiState", 1)[0]
        self.assertIn("if (!primaryError) throw cleanupError", minio)
        identity = self.source.split("async function runIdentity", 1)[1].split(
            "async function preflightIdentities", 1
        )[0]
        self.assertIn("if (!primaryError) throw cleanupError", identity)
        installation = self.source.split(
            "async function verifyPwaInstallation", 1
        )[1].split("async function execute", 1)[0]
        self.assertIn("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY", installation)
        self.assertNotIn('cdp.call("PWA.install"', installation)
        self.assertNotIn('cdp.call("PWA.launch"', installation)
        self.assertNotIn('"PWA.uninstall"', installation)
        self.assertLess(
            installation.index("if (primaryError)"),
            installation.index("if (cleanupFailed)"),
        )
        launch = self.source.split("async function launchChrome", 1)[1].split(
            "function terminateChrome", 1
        )[0]
        self.assertIn(".catch(() => undefined)", launch)
        self.assertLess(launch.index("cleanupChrome"), launch.index("throw error"))
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
