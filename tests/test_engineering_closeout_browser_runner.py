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
        self.assertIn('new Set(["all", "business", "faults", "pwa-update", "pwa-os", "material-rag-uat", "material-rag-uat-human", "analysis-report-uat", "analysis-report-workflow"])', self.source)
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
            '"material-rag-uat": "LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK"',
            '"material-rag-uat-human": "LOCAL_MATERIAL_RAG_UAT_HUMAN_SESSION_READY"',
            '"analysis-report-uat": "LOCAL_ANALYSIS_REPORT_DUAL_IDENTITY_BROWSER_OK"',
            '"analysis-report-workflow": "LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK"',
        ):
            self.assertIn(tag, self.source)
        self.assertNotIn('"pwa-os": "LOCAL_PWA_OS_VERIFY_OK"', self.source)
        for function in (
            "async function executeAll",
            "async function executeBusiness",
            "async function executeFaults",
            "async function executePwaUpdate",
            "async function executePwaOs",
            "async function executeMaterialRagUat",
            "async function executeAnalysisReportUat",
            "async function executeAnalysisReportWorkflow",
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
        self.assertNotIn("executeMaterialRagUat", all_stage)
        self.assertNotIn("executeAnalysisReportUat", all_stage)
        self.assertNotIn("executeAnalysisReportWorkflow", all_stage)

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
        self.assertIn('if (stage === "analysis-report-uat") return ANALYSIS_REPORT_IDENTITIES', self.source)
        self.assertIn('if (stage === "analysis-report-workflow") return ANALYSIS_REPORT_WORKFLOW_IDENTITIES', self.source)
        self.assertIn('if (stage === "material-rag-uat" || stage === "material-rag-uat-human") return [IDENTITIES[0]]', self.source)

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
        uat_stage = self.source.split("async function executeMaterialRagUat", 1)[1].split(
            "async function executeStage", 1
        )[0]
        self.assertIn("LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK", self.source)
        self.assertIn("headerPresent", self.source)
        self.assertIn("summarizeUiAskHeaders", uat_stage)
        self.assertIn("authorization_header_present", uat_stage)
        self.assertIn("enterprise_header_present", uat_stage)
        self.assertIn("uat_actor_header_present", uat_stage)
        self.assertNotIn("X-Uat-Actor", uat_stage)
        self.assertIn("journeys_passed", uat_stage)
        self.assertIn("cleared_on_failure", uat_stage)
        self.assertIn("residual_count", uat_stage)
        self.assertIn("valid_tenant_count", uat_stage)
        self.assertIn("cross_tenant_state_isolated", uat_stage)
        self.assertIn("cross_tenant_citation_denied", uat_stage)
        self.assertIn("cross_tenant_delete_isolated", uat_stage)
        self.assertIn("UAT_SEED_ENTERPRISE_A", uat_stage)
        self.assertIn("UAT_SEED_ENTERPRISE_B", uat_stage)
        self.assertIn("20000000-0000-4000-8000-00000000000a", self.source)
        self.assertIn("20000000-0000-4000-8000-00000000000b", self.source)
        self.assertNotIn("ids.sort()", uat_stage)
        self.assertIn("UAT_CLIENT_A_NAME", uat_stage)
        self.assertIn("UAT-SYNTH-CLIENT-A", self.source)
        self.assertIn("tenantDisplayValue", self.source)
        self.assertIn("limitedTenantSwitchEvidence", self.source)
        self.assertIn("inspectTenantSwitchLocate", self.source)
        self.assertIn('switchMembershipTenant(page,', uat_stage)
        self.assertIn('"A0"', uat_stage)
        self.assertIn('"B1"', uat_stage)
        self.assertIn('"A2"', uat_stage)
        self.assertIn('"B3"', uat_stage)
        self.assertNotIn('switchMembershipTenant(UAT_SEED_ENTERPRISE_A, "Local Enterprise A")', uat_stage)
        self.assertNotIn('switchMembershipTenant(UAT_SEED_ENTERPRISE_B, "Local Enterprise B")', uat_stage)
        tenant_switch = self.source.split("async function switchMembershipTenant", 1)[1].split(
            "async function executeMaterialRagUat", 1
        )[0]
        self.assertNotIn("clickElementWithText", tenant_switch)
        self.assertNotIn("startsWith(wanted)", tenant_switch)
        self.assertNotIn("dropdowns.length !== 1", tenant_switch)
        self.assertNotIn("matches[0].click()", tenant_switch)
        self.assertIn("aria-controls", tenant_switch)
        self.assertIn("Input.dispatchMouseEvent", tenant_switch)
        self.assertIn("visible_dropdown_count", tenant_switch)
        self.assertIn("target_dropdown_count", tenant_switch)
        self.assertIn("target_option_count", tenant_switch)
        self.assertIn("async function executeMaterialRagUatHuman", self.source)
        self.assertIn("human_uat_url_ready", self.source)
        self.assertIn("J1_PROVIDER", uat_stage)
        self.assertIn("J2_CLIENT_A", uat_stage)
        self.assertIn("J3_COMBO_A", uat_stage)
        self.assertIn("J3_COMBO_B", uat_stage)
        self.assertIn("J4_CLIENT_B_EMPTY", uat_stage)
        self.assertIn("J6_FAIL_CLEAR", uat_stage)
        self.assertIn("runUiAskJourney", uat_stage)
        self.assertIn("uatUiAskSnapshots", self.source)
        self.assertIn("summarizeUiAskHeaders", uat_stage)
        self.assertNotIn("UAT_PHASE_MISSING", uat_stage)
        self.assertNotIn("waitForPhase(", uat_stage)
        self.assertIn("QUERY_NOT_COMMITTED", self.source)
        self.assertIn("ASK_NOT_AVAILABLE", self.source)
        self.assertIn("POST_NOT_OBSERVED", self.source)
        select_fn = self.source.split("async function selectClosedQuery", 1)[1].split(
            "async function clickAsk", 1
        )[0]
        self.assertNotIn("clickElementWithText", select_fn)
        self.assertIn("clickVerifiedQueryOptionExpression", select_fn)
        option_click = self.source.split("function clickVerifiedQueryOptionExpression", 1)[1].split(
            "async function selectClosedQuery", 1
        )[0]
        self.assertIn("match.click()", option_click)
        syntax = subprocess.run(
            ["node", "--check", str(RUNNER)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(syntax.returncode, 0, "BROWSER_RUNNER_NODE_SYNTAX_FAILED")


class MaterialRagUatJourneyGateTests(unittest.TestCase):
    def _run(self, case: str) -> subprocess.CompletedProcess[str]:
        import os

        harness = f"""
import {{
  navigateQa,
  selectClosedQuery,
  clickAsk,
  runUiAskJourney,
  summarizeUiAskHeaders,
  VerifyError,
  UAT_QUERY_LABELS,
}} from {RUNNER.resolve().as_uri()!r};
import * as runner from {RUNNER.resolve().as_uri()!r};
const ASK_PATH = "/api/v1/local-uat/material-qa";

function delay(ms) {{
  return new Promise((resolve) => setTimeout(resolve, ms));
}}

function getComputedStyle(el) {{
  if (!el || el.hidden) return {{ display: "none", visibility: "hidden" }};
  return {{ display: "block", visibility: "visible" }};
}}

function selectedDisplayNode(page) {{
  const label = UAT_QUERY_LABELS[page.selectedQueryId];
  if (!label) return null;
  return {{
    textContent: label,
    getAttribute(name) {{
      return name === "title" ? label : null;
    }},
  }};
}}

function queryRoot(page) {{
  return {{
    className: page.dropdownOpen ? "ant-select ant-select-open" : "ant-select",
    classList: {{
      contains(name) {{
        return page.dropdownOpen && name === "ant-select-open";
      }},
    }},
    closest(sel) {{
      return String(sel).includes("ant-select") ? this : null;
    }},
    querySelector(inner) {{
      const text = String(inner);
      if (text.includes("combobox")) {{
        return {{
          getAttribute(name) {{
            if (name === "aria-expanded") return page.dropdownOpen ? "true" : "false";
            return null;
          }},
        }};
      }}
      const wantsLegacy = text.includes("ant-select-selection-item");
      const wantsContent = text.includes("ant-select-content");
      if (page.selectDom === "antd6") {{
        if (!wantsContent) return null;
        return selectedDisplayNode(page);
      }}
      if (wantsLegacy || wantsContent) return selectedDisplayNode(page);
      return null;
    }},
  }};
}}

function defaultOptionSpecs() {{
  return Object.entries(UAT_QUERY_LABELS).map(([queryId, title]) => ({{
    queryId,
    title,
    content: title,
    hidden: false,
    disabled: false,
  }}));
}}

function makeOption(page, spec) {{
  return {{
    className: spec.disabled
      ? "ant-select-item ant-select-item-option ant-select-item-option-disabled"
      : "ant-select-item ant-select-item-option",
    hidden: Boolean(spec.hidden),
    getAttribute(name) {{
      if (name === "title") return spec.title ?? "";
      if (name === "aria-disabled") return spec.disabled ? "true" : "false";
      if (name === "aria-hidden") return spec.hidden ? "true" : "false";
      return null;
    }},
    getBoundingClientRect() {{
      if (spec.hidden) return {{ width: 0, height: 0, top: 0, left: 0 }};
      return {{ width: 280, height: 32, top: 80, left: 16 }};
    }},
    querySelector(inner) {{
      if (String(inner).includes("ant-select-item-option-content")) {{
        return {{ textContent: spec.content ?? spec.title ?? "" }};
      }}
      return null;
    }},
    closest(sel) {{
      return String(sel).includes("ant-select-item-option") ? this : null;
    }},
    click() {{
      if (spec.disabled || spec.hidden) return;
      page.optionClicks += 1;
      page.selectedQueryId = spec.queryId;
      page.dropdownOpen = false;
      page.eventLog.push(`option_click:${{spec.queryId}}`);
    }},
  }};
}}

function makeDropdown(page) {{
  const options = (page.optionSpecs || defaultOptionSpecs()).map((spec) => makeOption(page, spec));
  return {{
    className: page.dropdownHidden
      ? "ant-select-dropdown ant-select-dropdown-hidden"
      : "ant-select-dropdown",
    hidden: Boolean(page.dropdownHidden),
    getAttribute(name) {{
      if (name === "aria-hidden") return page.dropdownHidden ? "true" : "false";
      return null;
    }},
    getBoundingClientRect() {{
      if (page.dropdownHidden) return {{ width: 0, height: 0, top: 0, left: 0 }};
      return {{ width: 280, height: 220, top: 40, left: 16 }};
    }},
    querySelectorAll(selector) {{
      if (String(selector).includes("ant-select-item-option") && !String(selector).includes("content")) {{
        return options;
      }}
      return [];
    }},
    querySelector(selector) {{
      const all = this.querySelectorAll(selector);
      return all[0] || null;
    }},
  }};
}}

function makeDocument(page) {{
  return {{
    get readyState() {{ return page.readyState; }},
    documentElement: {{
      setAttribute(name, value) {{
        page.docAttrs[String(name)] = String(value);
      }},
      getAttribute(name) {{
        const key = String(name);
        return Object.prototype.hasOwnProperty.call(page.docAttrs, key) ? page.docAttrs[key] : null;
      }},
    }},
    querySelector(selector) {{
      const text = String(selector);
      if (text.includes("ant-layout-header") || text.includes("ant-layout-content")) return {{}};
      if (text.includes("material-rag-query") && text.includes("ant-select-selection-item") && !text.includes("ant-select-content")) {{
        if (page.selectDom === "antd6") return null;
        return selectedDisplayNode(page);
      }}
      if (text.includes("material-rag-query")) return queryRoot(page);
      if (text.includes("material-rag-ask")) {{
        if (page.askMissing) return null;
        return {{
          disabled: page.askDisabled,
          getAttribute(name) {{
            if (page.askDisabled && (name === "disabled" || name === "aria-disabled")) {{
              return name === "aria-disabled" ? "true" : "";
            }}
            return null;
          }},
        }};
      }}
      if (text.includes("material-rag-phase-")) {{
        return {{
          getAttribute(name) {{
            return name === "data-testid" ? `material-rag-phase-${{page.phase}}` : null;
          }},
        }};
      }}
      if (text.includes("material-rag-empty-answer")) {{
        return page.answerPresent ? null : {{}};
      }}
      if (text.includes("material-rag-answer")) {{
        return page.answerPresent ? {{ textContent: "" }} : null;
      }}
      if (text.includes("material-rag-citations")) {{
        return {{
          querySelectorAll(inner) {{
            if (String(inner).includes("ant-table-row")) {{
              return Array.from({{ length: page.citationRows }}, () => ({{}}));
            }}
            return [];
          }},
        }};
      }}
      return null;
    }},
    querySelectorAll(selector) {{
      const text = String(selector);
      if (text.includes("ant-select-dropdown")) {{
        if (!page.dropdownOpen) return [];
        if (page.extraDropdown) return [makeDropdown(page), makeDropdown(page)];
        return [makeDropdown(page)];
      }}
      const node = this.querySelector(selector);
      return node ? [node] : [];
    }},
  }};
}}

class FakePage {{
  constructor() {{
    this.origin = "http://127.0.0.1:64405";
    this.sessionId = "session";
    this.currentRoute = "/";
    this.readyState = "complete";
    this.location = {{ origin: this.origin, pathname: "/qa", search: "" }};
    this.selectedQueryId = "provider.shared";
    this.selectDom = "antd6";
    this.dropdownOpen = false;
    this.dropdownHidden = false;
    this.extraDropdown = false;
    this.optionSpecs = null;
    this.optionClicks = 0;
    this.eventLog = [];
    this.askDisabled = false;
    this.askMissing = false;
    this.phase = "empty";
    this.answerPresent = false;
    this.citationRows = 0;
    this.docAttrs = {{}};
    this.navigateCount = 0;
    this.apiInflight = new Set();
    this.apiResponseEvents = [];
    this.apiRequestEvents = [];
    this.uatUiAskSnapshots = [];
    this.uatHeaderSnapshots = [];
    this.navigateDelayMs = 0;
    this.waitTimeout = 400;
    this.pollMs = 10;
    const page = this;
    this.cdp = {{
      async call(method, params = {{}}) {{
        if (method !== "Page.navigate") return {{}};
        const url = new URL(params.url);
        const apply = () => {{
          page.navigateCount += 1;
          page.location.pathname = url.pathname;
          page.location.search = url.search;
          page.docAttrs = {{}};
          page.answerPresent = false;
          page.citationRows = 0;
          page.phase = "empty";
          const query = url.searchParams.get("query");
          if (query && UAT_QUERY_LABELS[query]) page.selectedQueryId = query;
        }};
        if (page.navigateDelayMs > 0) {{
          const wait = page.navigateDelayMs;
          page.navigateDelayMs = 0;
          setTimeout(apply, wait);
          return {{}};
        }}
        apply();
        return {{}};
      }},
    }};
  }}

  evalExpr(expression) {{
    const fn = new Function("location", "document", "getComputedStyle", `return (${{expression}});`);
    return fn(this.location, makeDocument(this), getComputedStyle);
  }}

  async evaluate(expression) {{
    return this.evalExpr(expression);
  }}

  async waitForExpression(expression, code, timeout = this.waitTimeout) {{
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {{
      try {{
        if (await this.evaluate(expression)) return;
      }} catch {{
        // Keep polling while the fake document is still catching up.
      }}
      await delay(this.pollMs);
    }}
    throw new VerifyError(code);
  }}

  async waitForApiIdle() {{
    return;
  }}

  async clickElement(selector, code) {{
    const text = String(selector);
    if (text.includes("material-rag-query")) {{
      this.dropdownOpen = true;
      return;
    }}
    if (text.includes("material-rag-ask")) {{
      if (this.askMissing) throw new VerifyError(code);
      this.emitAsk?.();
      return;
    }}
    throw new VerifyError(code);
  }}

  async clickElementWithText() {{
    throw new Error("CLICK_ELEMENT_WITH_TEXT_FORBIDDEN_IN_QUERY_GATE");
  }}
}}

function evidenceOf(error) {{
  return error instanceof VerifyError ? error.evidence : null;
}}

function requireEvidence(error, code, journey, stage) {{
  if (!(error instanceof VerifyError) || error.code !== code) throw error;
  const evidence = evidenceOf(error);
  const keys = evidence ? Object.keys(evidence).sort().join(",") : "";
  if (keys !== "action_stage,actual_phase,expected_phase,http_status,journey,request_seen") {{
    throw new Error("EVIDENCE_KEYS_INVALID");
  }}
  if (
    evidence.journey !== journey
    || evidence.request_seen !== 0
    || evidence.action_stage !== stage
  ) {{
    throw new Error("EVIDENCE_INVALID");
  }}
}}

function emitHttp(page, status, phase) {{
  page.emitAsk = () => {{
    const requestId = `req-${{page.apiRequestEvents.length + 1}}`;
    page.eventLog.push("post");
    page.apiRequestEvents.push({{
      path: "/api/v1/local-uat/material-qa",
      method: "POST",
      requestId,
      authorization: true,
      enterprise: true,
      actor: false,
    }});
    page.eventLog.push(`http:${{status}}`);
    page.apiResponseEvents.push({{
      path: "/api/v1/local-uat/material-qa",
      method: "POST",
      status,
      requestId,
    }});
    page.phase = phase;
  }};
}}

async function main() {{
  const page = new FakePage();
  const caseName = process.env.GATE_CASE;
  if (caseName === "delayed-search") {{
    page.navigateDelayMs = 80;
    let done = false;
    const pending = navigateQa(page, "?client=1").then(() => {{
      done = true;
    }});
    await delay(20);
    if (done) throw new Error("NAVIGATE_PASSED_EARLY");
    await pending;
    if (page.location.search !== "?client=1") throw new Error("SEARCH_NOT_COMMITTED");
    return;
  }}
  if (caseName === "initial-empty") {{
    page.phase = "empty";
    page.emitAsk = null;
    try {{
      await runUiAskJourney(page, "J4_CLIENT_B_EMPTY", {{ search: "?client=2&query=client.current" }});
    }} catch (error) {{
      requireEvidence(error, "POST_NOT_OBSERVED", "J4_CLIENT_B_EMPTY", "observe_request");
      return;
    }}
    throw new Error("EMPTY_FALSE_GREEN");
  }}
  if (caseName === "query-uncommitted") {{
    page.optionSpecs = defaultOptionSpecs().map((spec) => (
      spec.queryId === "fail.clear" ? {{ ...spec, hidden: true }} : spec
    ));
    try {{
      await runUiAskJourney(page, "J6_FAIL_CLEAR");
    }} catch (error) {{
      requireEvidence(error, "QUERY_NOT_COMMITTED", "J6_FAIL_CLEAR", "select");
      if (page.selectedQueryId !== "provider.shared") throw new Error("QUERY_FALSE_COMMIT");
      if (page.optionClicks !== 0) throw new Error("HIDDEN_CLICKED");
      return;
    }}
    throw new Error("QUERY_FALSE_COMMIT");
  }}
  if (caseName === "ask-disabled") {{
    page.askDisabled = true;
    try {{
      await runUiAskJourney(page, "J1_PROVIDER");
    }} catch (error) {{
      requireEvidence(error, "ASK_NOT_AVAILABLE", "J1_PROVIDER", "ask");
      if (page.optionClicks !== 0) throw new Error("ALREADY_SELECTED_COUNTED_AS_CLICK");
      return;
    }}
    throw new Error("DISABLED_CLICKED");
  }}
  if (caseName === "antd6-content") {{
    page.selectDom = "antd6";
    const clicks = page.optionClicks;
    await selectClosedQuery(page, "provider.shared");
    if (page.selectedQueryId !== "provider.shared") throw new Error("ANTD6_LABEL_NOT_COMMITTED");
    if (page.optionClicks !== clicks) throw new Error("ALREADY_SELECTED_COUNTED_AS_CLICK");
    return;
  }}
  if (caseName === "http-classify") {{
    const trials = [
      ["J1_PROVIDER", 401, "ready", "HTTP_401"],
      ["J1_PROVIDER", 404, "denied", "HTTP_404"],
      ["J1_PROVIDER", 503, "unavailable", "HTTP_503"],
      ["J1_PROVIDER", 200, "denied", "UI_GOT_denied"],
      ["J6_FAIL_CLEAR", 503, "unavailable", null],
      ["J1_PROVIDER", 200, "ready", null],
    ];
    for (const [journey, status, phase, expected] of trials) {{
      const trial = new FakePage();
      emitHttp(trial, status, phase);
      try {{
        await runUiAskJourney(trial, journey);
        if (expected) throw new Error(`EXPECTED_${{expected}}`);
      }} catch (error) {{
        if (!expected) throw error;
        if (!(error instanceof VerifyError) || error.code !== expected) throw error;
        const evidence = evidenceOf(error);
        if (evidence?.http_status !== status || evidence.request_seen !== 1) {{
          throw new Error("HTTP_EVIDENCE_INVALID");
        }}
        if (evidence.action_stage !== "observe_request") throw new Error("HTTP_STAGE_INVALID");
      }}
    }}
    const hung = new FakePage();
    emitHttp(hung, 200, "loading");
    try {{
      await runUiAskJourney(hung, "J1_PROVIDER");
    }} catch (error) {{
      if (error instanceof VerifyError && error.code === "UI_NO_TERMINAL") return;
      throw error;
    }}
    throw new Error("LOADING_FALSE_GREEN");
  }}
  if (caseName === "headers-ui-only") {{
    page.uatHeaderSnapshots.push({{
      authorization: true,
      enterprise: true,
      actor: true,
    }});
    page.apiRequestEvents.push({{
      path: "/api/v1/local-uat/material-qa",
      method: "POST",
      authorization: true,
      enterprise: true,
      actor: true,
    }});
    emitHttp(page, 200, "ready");
    await runUiAskJourney(page, "J1_PROVIDER");
    const summary = summarizeUiAskHeaders(page);
    if (summary.authorization_header_present !== 1) throw new Error("AUTH_MISSING");
    if (summary.enterprise_header_present !== 1) throw new Error("ENTERPRISE_MISSING");
    if (summary.uat_actor_header_present !== 0) throw new Error("UATPOST_HEADER_POLLUTION");
    return;
  }}
  if (caseName === "wrapper-commit") {{
    if (page.selectedQueryId !== "provider.shared") throw new Error("INITIAL_NOT_PROVIDER");
    await selectClosedQuery(page, "fail.clear");
    if (page.selectedQueryId !== "fail.clear") throw new Error("WRAPPER_DID_NOT_COMMIT");
    if (page.optionClicks !== 1) throw new Error("WRAPPER_CLICK_COUNT");
    return;
  }}
  if (caseName === "refuse-bad-options") {{
    const failLabel = UAT_QUERY_LABELS["fail.clear"];
    const trials = [
      {{ optionSpecs: defaultOptionSpecs().map((spec) => spec.queryId === "fail.clear" ? {{ ...spec, hidden: true }} : spec) }},
      {{ optionSpecs: [...defaultOptionSpecs(), {{ queryId: "fail.clear", title: failLabel, content: failLabel, hidden: false, disabled: false }}] }},
      {{ optionSpecs: defaultOptionSpecs().map((spec) => spec.queryId === "fail.clear" ? {{ ...spec, disabled: true }} : spec) }},
      {{ optionSpecs: defaultOptionSpecs().map((spec) => spec.queryId === "fail.clear" ? {{ ...spec, title: "其他场景", content: "其他场景" }} : spec) }},
    ];
    for (const trial of trials) {{
      const probe = new FakePage();
      probe.optionSpecs = trial.optionSpecs;
      try {{
        await selectClosedQuery(probe, "fail.clear");
      }} catch (error) {{
        requireEvidence(error, "QUERY_NOT_COMMITTED", null, "select");
        if (probe.selectedQueryId !== "provider.shared") throw new Error("BAD_OPTION_COMMITTED");
        continue;
      }}
      throw new Error("BAD_OPTION_ACCEPTED");
    }}
    return;
  }}
  if (caseName === "stage-codes") {{
    const selectPage = new FakePage();
    selectPage.dropdownHidden = true;
    try {{
      await runUiAskJourney(selectPage, "J6_FAIL_CLEAR");
    }} catch (error) {{
      requireEvidence(error, "QUERY_NOT_COMMITTED", "J6_FAIL_CLEAR", "select");
    }}
    const askPage = new FakePage();
    askPage.askDisabled = true;
    try {{
      await runUiAskJourney(askPage, "J1_PROVIDER");
    }} catch (error) {{
      requireEvidence(error, "ASK_NOT_AVAILABLE", "J1_PROVIDER", "ask");
    }}
    const postPage = new FakePage();
    postPage.emitAsk = null;
    try {{
      await runUiAskJourney(postPage, "J1_PROVIDER");
    }} catch (error) {{
      requireEvidence(error, "POST_NOT_OBSERVED", "J1_PROVIDER", "observe_request");
    }}
    return;
  }}
  if (caseName === "j6-success") {{
    if (page.selectedQueryId !== "provider.shared") throw new Error("INITIAL_NOT_PROVIDER");
    emitHttp(page, 503, "unavailable");
    const evidence = await runUiAskJourney(page, "J6_FAIL_CLEAR");
    if (page.optionClicks < 1 || page.selectedQueryId !== "fail.clear") throw new Error("J6_NOT_CLICK_COMMITTED");
    if (evidence.request_seen !== 1 || evidence.http_status !== 503 || evidence.actual_phase !== "unavailable") {{
      throw new Error("J6_GATES_INVALID");
    }}
    const joined = page.eventLog.join(",");
    if (joined.includes("option_click:fail.clear,post,http:503") === false) throw new Error("J6_ORDER");
    return;
  }}
  if (caseName === "j6-fresh-empty-not-cleared") {{
    page.forceEmptySurface = true;
    page.emitAsk = () => {{
      page.eventLog.push("post");
      page.apiRequestEvents.push({{
        path: ASK_PATH,
        method: "POST",
        requestId: "req-empty",
        authorization: true,
        enterprise: true,
        actor: false,
      }});
      page.apiResponseEvents.push({{
        path: ASK_PATH,
        method: "POST",
        status: 200,
        requestId: "req-empty",
      }});
      page.phase = "ready";
      page.answerPresent = false;
      page.citationRows = 0;
      page.eventLog.push("http:200");
    }};
    if (typeof runner.runJ6FailClear === "function") {{
      try {{
        const summary = await runner.runJ6FailClear(page);
        if (summary && summary.cleared_on_failure === true) throw new Error("FRESH_EMPTY_FALSE_GREEN");
      }} catch (error) {{
        if (error instanceof VerifyError && error.code === "J6_PRIOR_ANSWER_MISSING") return;
        throw error;
      }}
      throw new Error("FRESH_EMPTY_FALSE_GREEN");
    }}
    emitHttp(page, 503, "unavailable");
    await runUiAskJourney(page, "J6_FAIL_CLEAR");
    const empty = await page.evaluate(`Boolean(document.querySelector("[data-testid='material-rag-empty-answer']"))`);
    if (empty === true) throw new Error("FRESH_EMPTY_FALSE_GREEN");
    return;
  }}
  if (caseName === "j6-same-document-prior-then-clear") {{
    if (typeof runner.runJ6FailClear !== "function") throw new Error("J6_CLEAR_FN_MISSING");
    let n = 0;
    page.emitAsk = () => {{
      n += 1;
      const id = `req-${{n}}`;
      page.eventLog.push("post");
      page.apiRequestEvents.push({{
        path: ASK_PATH,
        method: "POST",
        requestId: id,
        authorization: true,
        enterprise: true,
        actor: false,
      }});
      if (n === 1) {{
        page.apiResponseEvents.push({{
          path: ASK_PATH,
          method: "POST",
          status: 200,
          requestId: id,
        }});
        page.phase = "ready";
        page.answerPresent = true;
        page.citationRows = 1;
        page.eventLog.push("http:200");
        return;
      }}
      page.apiResponseEvents.push({{
        path: ASK_PATH,
        method: "POST",
        status: 503,
        requestId: id,
      }});
      page.phase = "unavailable";
      page.answerPresent = false;
      page.citationRows = 0;
      page.eventLog.push("http:503");
    }};
    const summary = await runner.runJ6FailClear(page);
    if (page.navigateCount !== 1) throw new Error("J6_NAVIGATED_BETWEEN_STEPS");
    if (summary.j6_prior_answer !== 1) throw new Error("J6_PRIOR_ANSWER");
    if (summary.j6_prior_citations !== 1) throw new Error("J6_PRIOR_CITATIONS");
    if (summary.j6_same_document !== 1) throw new Error("J6_SAME_DOCUMENT");
    if (summary.j6_answer_cleared !== 1) throw new Error("J6_ANSWER_CLEARED");
    if (summary.j6_citations_cleared !== 1) throw new Error("J6_CITATIONS_CLEARED");
    if (summary.cleared_on_failure !== true) throw new Error("J6_CLEARED_NOT_COMPUTED");
    return;
  }}
  if (caseName === "j6-requestid-mismatch") {{
    page.emitAsk = () => {{
      page.eventLog.push("post");
      page.apiRequestEvents.push({{
        path: ASK_PATH,
        method: "POST",
        requestId: "req-a",
        authorization: true,
        enterprise: true,
        actor: false,
      }});
      page.apiResponseEvents.push({{
        path: ASK_PATH,
        method: "POST",
        status: 503,
        requestId: "req-b",
      }});
      page.phase = "unavailable";
      page.eventLog.push("http:503");
    }};
    try {{
      await runUiAskJourney(page, "J6_FAIL_CLEAR");
    }} catch (error) {{
      if (error instanceof VerifyError && error.code === "REQUEST_RESPONSE_ID_MISMATCH") {{
        const evidence = evidenceOf(error);
        if (evidence?.action_stage !== "observe_request") throw new Error("MISMATCH_STAGE");
        return;
      }}
      throw error;
    }}
    throw new Error("ID_MISMATCH_FALSE_GREEN");
  }}
  if (caseName === "j6-summary-keys") {{
    if (typeof runner.computeJ6Clearance !== "function") throw new Error("J6_SUMMARY_FN_MISSING");
    const hardcoded = runner.computeJ6Clearance(
      {{ answer_present: 0, citation_rows: 0 }},
      {{ answer_present: 0, citation_rows: 0 }},
      1,
    );
    if (hardcoded.cleared_on_failure === true) throw new Error("CLEARED_HARDCODED_TRUE");
    if (hardcoded.j6_prior_answer !== 0) throw new Error("PRIOR_ANSWER_NOT_OBSERVED");
    const extra = runner.computeJ6Clearance(
      {{ answer_present: 1, citation_rows: 1, extra: 1 }},
      {{ answer_present: 0, citation_rows: 0 }},
      1,
    );
    const keys = Object.keys(extra).sort().join(",");
    if (keys !== "cleared_on_failure,j6_answer_cleared,j6_citations_cleared,j6_prior_answer,j6_prior_citations,j6_same_document") {{
      throw new Error("J6_SUMMARY_KEYS_INVALID");
    }}
    if (extra.cleared_on_failure !== true) throw new Error("J6_SUMMARY_NOT_COMPUTED");
    return;
  }}
  throw new Error("UNKNOWN_CASE");
}}

main().catch((error) => {{
  const code = error instanceof VerifyError ? error.code : error.message;
  process.stderr.write(`${{String(code)}}\\n`);
  process.exitCode = 1;
}});
"""
        return subprocess.run(
            ["node", "--input-type=module", "-e", harness],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "GATE_CASE": case},
        )

    def _assert_ok(self, case: str) -> None:
        completed = self._run(case)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_delayed_search_navigation_does_not_pass_early(self) -> None:
        self._assert_ok("delayed-search")

    def test_initial_empty_does_not_false_green(self) -> None:
        self._assert_ok("initial-empty")

    def test_uncommitted_query_blocks_before_request(self) -> None:
        self._assert_ok("query-uncommitted")

    def test_disabled_ask_blocks_before_request(self) -> None:
        self._assert_ok("ask-disabled")

    def test_antd6_content_selected_value_is_accepted(self) -> None:
        self._assert_ok("antd6-content")

    def test_http_and_phase_failures_are_classified(self) -> None:
        self._assert_ok("http-classify")

    def test_headers_count_only_ui_click_boundary(self) -> None:
        self._assert_ok("headers-ui-only")

    def test_visible_unique_enabled_wrapper_commits_fail_clear(self) -> None:
        self._assert_ok("wrapper-commit")

    def test_hidden_duplicate_disabled_or_wrong_label_does_not_commit(self) -> None:
        self._assert_ok("refuse-bad-options")

    def test_select_ask_observe_stages_use_distinct_codes(self) -> None:
        self._assert_ok("stage-codes")

    def test_j6_success_requires_post_503_unavailable_in_order(self) -> None:
        self._assert_ok("j6-success")

    def test_j6_fresh_empty_does_not_pass_cleared_on_failure(self) -> None:
        self._assert_ok("j6-fresh-empty-not-cleared")

    def test_j6_same_document_requires_prior_ready_then_fail_clear(self) -> None:
        self._assert_ok("j6-same-document-prior-then-clear")

    def test_j6_mismatched_network_request_id_fails(self) -> None:
        self._assert_ok("j6-requestid-mismatch")

    def test_j6_summary_rejects_hardcoded_missing_or_extra_fields(self) -> None:
        self._assert_ok("j6-summary-keys")
        source = (ROOT / "src/web/scripts/engineering-browser-verify.mjs").read_text(encoding="utf-8")
        self.assertIn("j6_prior_answer", source)
        self.assertIn("j6_prior_citations", source)
        self.assertIn("j6_same_document", source)
        self.assertIn("j6_answer_cleared", source)
        self.assertIn("j6_citations_cleared", source)
        self.assertNotIn("cleared_on_failure: true", source)
        self.assertIn("REQUEST_RESPONSE_ID_MISMATCH", source)
        self.assertIn("runJ6FailClear", source)
        self.assertIn("computeJ6Clearance", source)

    def test_localctl_j6_summary_rejects_missing_extra_or_wrong_values(self) -> None:
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader(
            "anhuan_localctl_j6_summary", str(ROOT / "scripts/localctl")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        validate = module._validate_material_rag_uat_browser_summary
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        browser_fn = localctl.split("def _material_rag_uat_browser(", 1)[1].split(
            "def _material_rag_uat_print_url", 1
        )[0]
        self.assertIn("_validate_material_rag_uat_browser_summary", browser_fn)
        base = {
            "stage": "material-rag-uat",
            "journeys_passed": 6,
            "cleared_on_failure": True,
            "residual_count": 0,
            "authorization_header_present": 1,
            "enterprise_header_present": 1,
            "uat_actor_header_present": 0,
            "denied_404": 1,
            "conflict_409": 1,
            "unavailable_503": 1,
            "valid_tenant_count": 2,
            "cross_tenant_state_isolated": 1,
            "cross_tenant_citation_denied": 2,
            "cross_tenant_delete_isolated": 1,
            "human_uat_url_ready": 1,
            "j6_prior_answer": 1,
            "j6_prior_citations": 1,
            "j6_same_document": 1,
            "j6_answer_cleared": 1,
            "j6_citations_cleared": 1,
        }
        validate(base)
        missing = dict(base)
        missing.pop("j6_prior_answer")
        with self.assertRaises(module.LocalError) as raised_missing:
            validate(missing)
        self.assertIn("J6_CLEARANCE_INVALID", str(raised_missing.exception))
        extra = dict(base)
        extra["j6_extra"] = 1
        with self.assertRaises(module.LocalError) as raised_extra:
            validate(extra)
        self.assertIn("SUMMARY_INVALID", str(raised_extra.exception))
        hardcoded = dict(base)
        hardcoded["j6_prior_answer"] = 0
        hardcoded["cleared_on_failure"] = True
        with self.assertRaises(module.LocalError) as raised_hard:
            validate(hardcoded)
        self.assertIn("J6_CLEARANCE_INVALID", str(raised_hard.exception))


TENANT_SWITCH_HARNESS = r"""
import * as runner from __RUNNER_URI__;

const MEMBER_A = {
  enterprise_id: "20000000-0000-4000-8000-00000000000a",
  name: "Local Enterprise A",
  role: "enterprise_admin",
};
const MEMBER_B = {
  enterprise_id: "20000000-0000-4000-8000-00000000000b",
  name: "Local Enterprise B",
  role: "enterprise_admin",
};
const SELECTED_KEY = "f1-selected-enterprise";
const TENANT_EVIDENCE_KEYS = "action,step,target_dropdown_count,target_option_count,visible_dropdown_count";

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getComputedStyle(el) {
  if (!el || el.hidden) return { display: "none", visibility: "hidden" };
  return { display: "block", visibility: "visible" };
}

function displayOf(member) {
  return `${member.name} (${member.role})`;
}

function requireTenantEvidence(error, family, step, action) {
  if (!(error instanceof runner.VerifyError)) throw error;
  const expected = new RegExp(
    `^UAT_TENANT_${family}_${step}_${String(action).toUpperCase()}_V\\d+_T\\d+_O\\d+$`,
  );
  if (!expected.test(error.code)) throw new Error(`TENANT_CODE_INVALID ${error.code}`);
  const evidence = error.evidence;
  const keys = evidence ? Object.keys(evidence).sort().join(",") : "";
  if (keys !== TENANT_EVIDENCE_KEYS) throw new Error("TENANT_EVIDENCE_KEYS_INVALID");
  if (evidence.step !== step || evidence.action !== action) throw new Error("TENANT_EVIDENCE_INVALID");
  const blob = JSON.stringify(evidence);
  if (/20000000|Local Enterprise|oidc_|Bearer|token/i.test(blob)) throw new Error("TENANT_EVIDENCE_LEAK");
  return evidence;
}

function makeOption(page, spec, kind, index) {
  const left = kind === "qa" ? 16 : 400;
  const top = 90 + (Number(index) || 0) * 36;
  const box = spec.hidden
    ? { width: 0, height: 0, top: 0, left: 0 }
    : { width: 180, height: 32, top, left };
  return {
    className: spec.disabled
      ? "ant-select-item ant-select-item-option ant-select-item-option-disabled"
      : "ant-select-item ant-select-item-option",
    hidden: Boolean(spec.hidden),
    textContent: spec.content ?? spec.title ?? "",
    getAttribute(name) {
      if (name === "title") return spec.title ?? "";
      if (name === "aria-disabled") return spec.disabled ? "true" : "false";
      if (name === "aria-hidden") return spec.hidden ? "true" : "false";
      return null;
    },
    getBoundingClientRect() {
      return box;
    },
    querySelector(inner) {
      if (String(inner).includes("ant-select-item-option-content")) {
        return { textContent: spec.content ?? spec.title ?? "" };
      }
      return null;
    },
    closest(sel) {
      const text = String(sel);
      if (text.includes("ant-select-item-option")) return this;
      if (text.includes("ant-select-dropdown")) return kind === "qa" ? page._qaDropdown : page._tenantDropdown;
      return null;
    },
    contains(node) {
      return node === this;
    },
    scrollIntoView() {},
    click() {
      if (spec.disabled || spec.hidden) return;
      if (kind === "qa") {
        page.qaClicks += 1;
        return;
      }
      page.optionClicks += 1;
      if (page.commitOnClick === false) return;
      page.store[SELECTED_KEY] = spec.enterpriseId;
      page.headerTitle = spec.content ?? spec.title;
      page.headerTitleAttr = spec.enterpriseId;
    },
  };
}

function makeDropdown(page, kind, options) {
  const hidden = kind === "tenant" ? !page.tenantPortalReady : false;
  const dropdown = {
    className: hidden ? "ant-select-dropdown ant-select-dropdown-hidden" : "ant-select-dropdown",
    hidden,
    id: kind === "tenant" ? "tenant-dropdown" : "qa-dropdown",
    getAttribute(name) {
      if (name === "aria-hidden") return hidden ? "true" : "false";
      if (name === "id") return dropdown.id;
      return null;
    },
    getBoundingClientRect() {
      if (hidden) return { width: 0, height: 0, top: 0, left: 0 };
      return { width: 220, height: 180, top: 64, left: kind === "qa" ? 16 : 400 };
    },
    querySelectorAll(selector) {
      if (String(selector).includes("ant-select-item-option") && !String(selector).includes("content")) {
        return options;
      }
      return [];
    },
    querySelector(selector) {
      const all = this.querySelectorAll(selector);
      return all[0] || null;
    },
    closest(sel) {
      return String(sel).includes("ant-select-dropdown") ? this : null;
    },
  };
  for (const option of options) {
    option._dropdown = dropdown;
    const previous = option.closest;
    option.closest = (sel) => {
      if (String(sel).includes("ant-select-dropdown")) return dropdown;
      return previous.call(option, sel);
    };
  }
  return dropdown;
}

class FakePage {
  constructor() {
    this.sessionId = "session";
    this.waitTimeout = 400;
    this.pollMs = 10;
    this.headerExpanded = false;
    this.tenantPortalReady = false;
    this.tenantPortalDelayMs = 0;
    this.leftoverQa = false;
    this.ariaControls = "tenant-listbox";
    this.commitOnClick = true;
    this.optionClicks = 0;
    this.qaClicks = 0;
    this.physicalClicks = 0;
    this.mouseEvents = [];
    this.tenantOptions = [
      {
        title: displayOf(MEMBER_A),
        content: displayOf(MEMBER_A),
        enterpriseId: MEMBER_A.enterprise_id,
        hidden: false,
        disabled: false,
      },
      {
        title: displayOf(MEMBER_B),
        content: displayOf(MEMBER_B),
        enterpriseId: MEMBER_B.enterprise_id,
        hidden: false,
        disabled: false,
      },
    ];
    this.store = { [SELECTED_KEY]: MEMBER_A.enterprise_id };
    this.headerTitle = displayOf(MEMBER_A);
    this.headerTitleAttr = MEMBER_A.enterprise_id;
    this.localStorage = {
      getItem: (key) => (Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null),
      setItem: (key, value) => {
        this.store[key] = String(value);
      },
    };
    const page = this;
    this.cdp = {
      async call(method, params = {}) {
        if (method !== "Input.dispatchMouseEvent") return {};
        page.mouseEvents.push(params.type);
        if (params.type === "mouseReleased") {
          page.physicalClicks += 1;
          const hit = page.hitOption(params.x, params.y);
          if (hit) hit.click();
        }
        return {};
      },
    };
  }

  hitOption(x, y) {
    const document = makeDocument(this);
    const dropdowns = document.querySelectorAll(".ant-select-dropdown");
    for (const dropdown of dropdowns) {
      for (const option of dropdown.querySelectorAll(".ant-select-item-option")) {
        const box = option.getBoundingClientRect();
        if (x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height) {
          return option;
        }
      }
    }
    return null;
  }

  evalExpr(expression) {
    const fn = new Function(
      "location",
      "document",
      "getComputedStyle",
      "localStorage",
      `return (${expression});`,
    );
    return fn({ pathname: "/qa" }, makeDocument(this), getComputedStyle, this.localStorage);
  }

  async evaluate(expression) {
    return this.evalExpr(expression);
  }

  async waitForExpression(expression, code, timeout = this.waitTimeout) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        if (await this.evaluate(expression)) return;
      } catch {
        // Keep polling while the fake portal is catching up.
      }
      await delay(this.pollMs);
    }
    throw new runner.VerifyError(code);
  }

  async clickElement(selector, code) {
    const text = String(selector);
    if (!(text.includes("ant-layout-header") && text.includes("ant-select"))) {
      throw new runner.VerifyError(code);
    }
    this.headerExpanded = true;
    if (this.tenantPortalDelayMs > 0) {
      const wait = this.tenantPortalDelayMs;
      this.tenantPortalDelayMs = 0;
      this.tenantPortalReady = false;
      setTimeout(() => {
        this.tenantPortalReady = true;
      }, wait);
      return;
    }
    this.tenantPortalReady = true;
  }
}

function makeDocument(page) {
  const qaOptions = [
    makeOption(page, { title: "fail.clear", content: "fail.clear", hidden: false, disabled: false }, "qa", 0),
  ];
  const tenantOptions = (page.tenantOptions || []).map((spec, index) => makeOption(page, spec, "tenant", index));
  const qaDropdown = page.leftoverQa ? makeDropdown(page, "qa", qaOptions) : null;
  const tenantDropdown = page.tenantPortalReady ? makeDropdown(page, "tenant", tenantOptions) : null;
  page._qaDropdown = qaDropdown;
  page._tenantDropdown = tenantDropdown;
  const listbox = tenantDropdown && {
    id: page.ariaControls,
    className: "",
    closest(sel) {
      return String(sel).includes("ant-select-dropdown") ? tenantDropdown : null;
    },
  };
  const combobox = {
    getAttribute(name) {
      if (name === "aria-expanded") return page.headerExpanded ? "true" : "false";
      if (name === "aria-controls") return page.ariaControls;
      return null;
    },
  };
  const selectedItem = {
    textContent: page.headerTitle,
    getAttribute(name) {
      if (name === "title") return page.headerTitleAttr != null ? page.headerTitleAttr : page.headerTitle;
      return null;
    },
  };
  const headerSelect = {
    className: "ant-select",
    querySelector(inner) {
      const text = String(inner);
      if (text.includes("combobox")) return combobox;
      if (text.includes("ant-select-selection-item") || text.includes("ant-select-content")) return selectedItem;
      return null;
    },
  };
  const header = {
    querySelector(inner) {
      const text = String(inner);
      if (text.includes("combobox")) return combobox;
      if (text.includes("ant-select") && !text.includes("dropdown")) return headerSelect;
      return null;
    },
  };
  return {
    getElementById(id) {
      if (listbox && id === page.ariaControls) return listbox;
      return null;
    },
    querySelector(selector) {
      const text = String(selector);
      if (text.includes("ant-layout-header") && text.includes("combobox")) return combobox;
      if (text === ".ant-layout-header" || (text.includes("ant-layout-header") && !text.includes("select"))) {
        return header;
      }
      if (text.includes("ant-layout-header") && text.includes("ant-select")) return headerSelect;
      return null;
    },
    querySelectorAll(selector) {
      const text = String(selector);
      if (text.includes("ant-select-dropdown")) {
        const out = [];
        if (qaDropdown) out.push(qaDropdown);
        if (tenantDropdown) out.push(tenantDropdown);
        return out;
      }
      return [];
    },
    elementFromPoint(x, y) {
      const dropdowns = this.querySelectorAll(".ant-select-dropdown");
      for (const dropdown of dropdowns) {
        for (const option of dropdown.querySelectorAll(".ant-select-item-option")) {
          const box = option.getBoundingClientRect();
          if (x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height) {
            return option;
          }
        }
      }
      return null;
    },
  };
}

function requireSwitchFn() {
  if (typeof runner.switchMembershipTenant !== "function") throw new Error("SWITCH_FN_MISSING");
  if (typeof runner.tenantDisplayValue !== "function") throw new Error("DISPLAY_FN_MISSING");
  if (typeof runner.limitedTenantSwitchEvidence !== "function") throw new Error("EVIDENCE_FN_MISSING");
}

async function expectFail(page, member, step, family, action) {
  try {
    await runner.switchMembershipTenant(page, member, step);
  } catch (error) {
    requireTenantEvidence(error, family, step, action);
    return error;
  }
  throw new Error("BAD_OPTION_FALSE_GREEN");
}

async function main() {
  requireSwitchFn();
  const caseName = process.env.GATE_CASE;
  if (caseName === "tenant-delayed-portal") {
    const page = new FakePage();
    page.tenantPortalDelayMs = 80;
    const started = Date.now();
    let failedEarly = false;
    const pending = runner.switchMembershipTenant(page, MEMBER_B, "B1").catch((error) => {
      if (Date.now() - started < 50) failedEarly = true;
      throw error;
    });
    await delay(20);
    if (failedEarly) throw new Error("TENANT_DELAYED_PORTAL_FAILED_EARLY");
    await pending;
    if (Date.now() - started < 50) throw new Error("TENANT_DELAYED_PORTAL_FAILED_EARLY");
    if (page.localStorage.getItem(SELECTED_KEY) !== MEMBER_B.enterprise_id) throw new Error("TENANT_NOT_COMMITTED");
    if (page.headerTitle !== displayOf(MEMBER_B)) throw new Error("HEADER_NOT_COMMITTED");
    return;
  }
  if (caseName === "tenant-leftover-qa") {
    const page = new FakePage();
    page.leftoverQa = true;
    await runner.switchMembershipTenant(page, MEMBER_B, "B1");
    if (page.qaClicks !== 0) throw new Error("QA_DROPDOWN_CLICKED");
    if (page.localStorage.getItem(SELECTED_KEY) !== MEMBER_B.enterprise_id) throw new Error("TENANT_NOT_COMMITTED");
    if (page.optionClicks + page.physicalClicks < 1) throw new Error("HEADER_OPTION_NOT_CLICKED");
    return;
  }
  if (caseName === "tenant-refuse-bad-options") {
    const hidden = new FakePage();
    hidden.tenantOptions = [
      { title: displayOf(MEMBER_B), content: displayOf(MEMBER_B), enterpriseId: MEMBER_B.enterprise_id, hidden: true, disabled: false },
    ];
    await expectFail(hidden, MEMBER_B, "B1", "OPTION", "locate");
    if (hidden.optionClicks !== 0 || hidden.physicalClicks !== 0) throw new Error("HIDDEN_CLICKED");

    const disabled = new FakePage();
    disabled.tenantOptions = [
      { title: displayOf(MEMBER_B), content: displayOf(MEMBER_B), enterpriseId: MEMBER_B.enterprise_id, hidden: false, disabled: true },
    ];
    await expectFail(disabled, MEMBER_B, "B1", "OPTION", "locate");
    if (disabled.optionClicks !== 0 || disabled.physicalClicks !== 0) throw new Error("DISABLED_CLICKED");

    const prefix = new FakePage();
    prefix.tenantOptions = [
      { title: MEMBER_B.name, content: MEMBER_B.name, enterpriseId: MEMBER_B.enterprise_id, hidden: false, disabled: false },
    ];
    await expectFail(prefix, MEMBER_B, "B1", "OPTION", "locate");
    if (prefix.optionClicks !== 0 || prefix.physicalClicks !== 0) throw new Error("PREFIX_FALSE_GREEN");

    const duplicate = new FakePage();
    duplicate.tenantOptions = [
      { title: displayOf(MEMBER_B), content: displayOf(MEMBER_B), enterpriseId: MEMBER_B.enterprise_id, hidden: false, disabled: false },
      { title: displayOf(MEMBER_B), content: displayOf(MEMBER_B), enterpriseId: MEMBER_B.enterprise_id, hidden: false, disabled: false },
    ];
    await expectFail(duplicate, MEMBER_B, "B1", "OPTION", "locate");
    if (duplicate.optionClicks !== 0 || duplicate.physicalClicks !== 0) throw new Error("DUPLICATE_CLICKED");
    return;
  }
  if (caseName === "tenant-commit-failed") {
    const page = new FakePage();
    page.commitOnClick = false;
    try {
      await runner.switchMembershipTenant(page, MEMBER_B, "B1");
    } catch (error) {
      const evidence = requireTenantEvidence(error, "SWITCH", "B1", "commit");
      if (evidence.target_option_count !== 1) throw new Error("COMMIT_COUNT_INVALID");
      if (page.localStorage.getItem(SELECTED_KEY) !== MEMBER_A.enterprise_id) throw new Error("STORAGE_CHANGED");
      return;
    }
    throw new Error("COMMIT_FALSE_GREEN");
  }
  if (caseName === "tenant-steps-evidence") {
    const aRole = tenantDisplayValueOrThrow(MEMBER_A);
    const bOtherRole = runner.tenantDisplayValue({ name: MEMBER_B.name, role: "auditor" });
    if (aRole !== "Local Enterprise A (enterprise_admin)") throw new Error("DISPLAY_NOT_FROM_MEMBERSHIP");
    if (bOtherRole !== "Local Enterprise B (auditor)") throw new Error("ROLE_HARDCODED");
    if (runner.tenantDisplayValue({ name: MEMBER_A.name, role: "" }) !== null) throw new Error("EMPTY_ROLE_ACCEPTED");
    const stripped = runner.limitedTenantSwitchEvidence({
      step: "B1",
      action: "locate",
      visible_dropdown_count: 2,
      target_dropdown_count: 1,
      target_option_count: 0,
      label: "Local Enterprise B",
      id: MEMBER_B.enterprise_id,
      token: "secret",
      journey: "J6_FAIL_CLEAR",
    });
    if (Object.keys(stripped).sort().join(",") !== TENANT_EVIDENCE_KEYS) throw new Error("STRIP_KEYS_INVALID");
    if (JSON.stringify(stripped).includes("Local Enterprise") || JSON.stringify(stripped).includes("secret")) {
      throw new Error("STRIP_LEAK");
    }
    const page = new FakePage();
    page.store[SELECTED_KEY] = "";
    page.headerTitle = "";
    await runner.switchMembershipTenant(page, MEMBER_A, "A0");
    await runner.switchMembershipTenant(page, MEMBER_B, "B1");
    await runner.switchMembershipTenant(page, MEMBER_A, "A2");
    await runner.switchMembershipTenant(page, MEMBER_B, "B3");
    if (page.localStorage.getItem(SELECTED_KEY) !== MEMBER_B.enterprise_id) throw new Error("STEPS_NOT_COMMITTED");
    return;
  }
  throw new Error("UNKNOWN_CASE");
}

function tenantDisplayValueOrThrow(member) {
  const value = runner.tenantDisplayValue(member);
  if (typeof value !== "string" || !value) throw new Error("DISPLAY_FN_MISSING");
  return value;
}

main().catch((error) => {
  const message = error instanceof runner.VerifyError
    ? `${error.code} ${JSON.stringify(error.evidence)}`
    : (error && error.stack) || String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
"""


class MaterialRagUatTenantSwitchTests(unittest.TestCase):
    def _run(self, case: str):
        import json
        import os

        harness = TENANT_SWITCH_HARNESS.replace("__RUNNER_URI__", json.dumps(RUNNER.resolve().as_uri()))
        return subprocess.run(
            ["node", "--input-type=module", "-e", harness],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "GATE_CASE": case},
        )

    def _assert_ok(self, case: str) -> None:
        completed = self._run(case)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_delayed_header_portal_does_not_fail_early(self) -> None:
        self._assert_ok("tenant-delayed-portal")

    def test_leftover_qa_dropdown_does_not_steal_header_listbox(self) -> None:
        self._assert_ok("tenant-leftover-qa")

    def test_hidden_disabled_prefix_or_duplicate_tenant_option_fail_closed(self) -> None:
        self._assert_ok("tenant-refuse-bad-options")

    def test_physical_click_without_storage_is_switch_failed(self) -> None:
        self._assert_ok("tenant-commit-failed")

    def test_tenant_switch_steps_and_limited_evidence(self) -> None:
        self._assert_ok("tenant-steps-evidence")


if __name__ == "__main__":
    unittest.main()
