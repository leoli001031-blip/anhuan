"""Single lightweight contract check for the P8 internal PWA prototype."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/web"
MANIFEST = WEB / "public/manifest.webmanifest"
WORKER = WEB / "public/pwa-sw.js"
FEATURE = WEB / "src/features/p8"


class P8InternalPwaContractTests(unittest.TestCase):
    def test_manifest_is_internal_local_and_standalone(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(payload["start_url"], "/internal-app")
        self.assertEqual(payload["scope"], "/")
        self.assertEqual(payload["display"], "standalone")
        self.assertEqual(payload["lang"], "zh-CN")
        self.assertEqual(len(payload["icons"]), 1)
        self.assertEqual(payload["icons"][0]["src"], "/pwa-icon.svg")
        self.assertNotIn("http://", MANIFEST.read_text(encoding="utf-8"))
        self.assertNotIn("https://", MANIFEST.read_text(encoding="utf-8"))

    def test_service_worker_refuses_api_auth_and_sensitive_requests(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        for token in (
            'request.method !== "GET"',
            "url.origin !== self.location.origin",
            'request.headers.has("Authorization")',
            'request.headers.has("Range")',
            'request.cache === "no-store"',
            'pathIsOrStartsWith(url.pathname, "/api")',
            'pathIsOrStartsWith(url.pathname, "/realms")',
            'pathIsOrStartsWith(url.pathname, "/callback")',
            "AUTH_QUERY_KEYS.some",
            'response.headers.has("Set-Cookie")',
            'vary.trim() !== "*"',
        ):
            self.assertIn(token, source)
        self.assertNotIn('addEventListener("sync"', source)
        self.assertNotIn('addEventListener("push"', source)

    def test_cache_and_update_operations_are_namespace_limited(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        registration = (FEATURE / "serviceWorkerRegistration.ts").read_text(encoding="utf-8")
        constants = (FEATURE / "constants.ts").read_text(encoding="utf-8")
        self.assertIn("anhuan-internal-pwa-", worker)
        self.assertIn("anhuan-internal-pwa-", constants)
        self.assertIn("INTERNAL_PWA_CACHE_PREFIX", registration)
        self.assertIn("key.startsWith(CACHE_PREFIX)", worker)
        self.assertIn("key.startsWith(INTERNAL_PWA_CACHE_PREFIX)", registration)
        self.assertNotIn("localStorage.clear", registration)
        self.assertNotIn("document.cookie", registration)
        self.assertIn("{ type: \"SKIP_WAITING\" }", constants)
        for token in (
            "installOfflineShell()",
            "html.matchAll",
            'url.pathname.startsWith("/assets/")',
            "P8_STATIC_RESPONSE_REJECTED",
            "await staticCache.put(request, response)",
        ):
            self.assertIn(token, worker)

    def test_registration_is_prod_secure_and_owned(self) -> None:
        source = (FEATURE / "serviceWorkerRegistration.ts").read_text(encoding="utf-8")
        for token in (
            "import.meta.env.PROD",
            "window.isSecureContext",
            'scope: "/"',
            'updateViaCache: "none"',
            "getRegistrations()",
            "isOwnedRegistration",
            "reloadOnNextController",
            'fetch("/api/healthz"',
            'cache: "no-store"',
            'credentials: "omit"',
            "response.status === 200",
            "navigator.onLine && apiReachable",
        ):
            self.assertIn(token, source)

    def test_app_page_badge_and_fixed_boundaries_are_wired(self) -> None:
        app = (WEB / "src/App.tsx").read_text(encoding="utf-8")
        layout = (WEB / "src/pages/Layout.tsx").read_text(encoding="utf-8")
        main = (WEB / "src/main.tsx").read_text(encoding="utf-8")
        self.assertIn('path="internal-app"', app)
        self.assertIn('key: "/internal-app"', layout)
        self.assertIn("<OnlineOfflineBadge />", layout)
        self.assertIn("registerInternalPwaServiceWorker", main)
        page = (FEATURE / "pages/InternalPwaPage.tsx").read_text(encoding="utf-8")
        self.assertIn('data-testid="pwa-apply-update"', page)
        self.assertIn('data-testid="pwa-check-update"', page)
        feature_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FEATURE.rglob("*.ts*")))
        for boundary in (
            "INTERNAL_PWA_ONLY",
            "NO_FORMAL_MINI_PROGRAM",
            "NO_PRODUCTION_PUBLISH",
            "ONLINE_DATA_ONLY",
            "NOT_PRODUCTION",
        ):
            self.assertIn(boundary, feature_source)

    def test_worker_syntax_and_frontend_types_without_build(self) -> None:
        node_check = subprocess.run(
            ["node", "--check", str(WORKER)],
            cwd=WEB,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(node_check.returncode, 0, "P8_SERVICE_WORKER_SYNTAX_FAILED")
        tsc = WEB / "node_modules/.bin/tsc"
        self.assertTrue(tsc.is_file(), "P8_TYPESCRIPT_COMPILER_MISSING")
        typecheck = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(WEB / "tsconfig.app.json")],
            cwd=WEB,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(typecheck.returncode, 0, "P8_TYPESCRIPT_TYPECHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
