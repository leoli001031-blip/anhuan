"""F1 observability tests against formal random API and Jaeger ports."""
from __future__ import annotations

import json
import unittest
import urllib.request

from f11_support import (
    configure_formal_runtime,
    formal_api_base,
    formal_jaeger_base,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F1ObservabilityTests(unittest.TestCase):
    def test_api_healthz(self) -> None:
        req = urllib.request.Request(f"{formal_api_base()}/healthz")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_trace_reaches_jaeger(self) -> None:
        # Trigger a request that produces spans.
        token = get_token()
        req = urllib.request.Request(
            f"{formal_api_base()}/api/v1/enterprises",
            headers={"Authorization": f"Bearer {token}"},
        )
        urllib.request.urlopen(req, timeout=15).read()
        import time

        time.sleep(3)
        url = (
            f"{formal_jaeger_base()}"
            "/api/traces?service=anhuan-f1-api&limit=10"
        )
        with urllib.request.urlopen(url, timeout=10) as response:
            body = json.loads(response.read())
        traces = body.get("data", [])
        self.assertGreater(len(traces), 0)
        ops = {s["operationName"] for t in traces for s in t.get("spans", [])}
        self.assertTrue(any("enterprises" in op for op in ops))


if __name__ == "__main__":
    unittest.main()
