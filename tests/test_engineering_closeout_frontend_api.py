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


if __name__ == "__main__":
    unittest.main()
