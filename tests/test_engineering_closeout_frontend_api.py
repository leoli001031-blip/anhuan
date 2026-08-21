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
        api_source = (WEB_SOURCE / "api.ts").read_text(encoding="utf-8")
        layout_source = (WEB_SOURCE / "pages" / "Layout.tsx").read_text(encoding="utf-8")

        for token in (
            "let tenantRequestController = new AbortController()",
            "let tenantRequestGeneration = 0",
            "previousController.abort()",
            "tenantRequestController = new AbortController()",
            "tenantRequestGeneration += 1",
            "mergeAbortSignals(options.signal, tenantRequestController.signal)",
            "signal: mergedSignal.signal",
            "generation !== tenantRequestGeneration",
            "assertTenantRequestCurrent(mergedSignal.signal, requestGeneration)",
            "mergedSignal.dispose()",
            "window.dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT))",
        ):
            self.assertIn(token, api_source)

        setter = api_source.split("export function setSelectedEnterprise", 1)[1].split(
            "export async function api", 1
        )[0]
        self.assertLess(setter.index("previousController.abort()"), setter.index("window.dispatchEvent"))
        self.assertLess(setter.index("tenantRequestGeneration += 1"), setter.index("window.dispatchEvent"))

        self.assertIn("window.addEventListener(ENTERPRISE_CHANGED_EVENT", layout_source)
        self.assertIn("<Outlet key={tenantEpoch} />", layout_source)
        self.assertNotIn("window.dispatchEvent", layout_source)

        event_emitters: list[str] = []
        for path in sorted(WEB_SOURCE.rglob("*")):
            if path.suffix not in {".ts", ".tsx"} or not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            if "dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT))" in source:
                event_emitters.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(event_emitters, ["src/web/src/api.ts"])


if __name__ == "__main__":
    unittest.main()
