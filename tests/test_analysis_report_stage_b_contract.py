"""Executable tenant-isolation behavior gates. Source-string checks are not enough."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "src" / "web" / "src"
HARNESS = r"""
import { pathToFileURL } from "node:url";

const ENTERPRISE_KEY = "f1-selected-enterprise";
const A = "20000000-0000-4000-8000-00000000000a";
const B = "20000000-0000-4000-8000-00000000000b";
const UNKNOWN = "ffffffff-ffff-4fff-8fff-ffffffffffff";

const store = new Map();
const listeners = new Map();
globalThis.localStorage = {
  getItem(key) { return store.has(key) ? store.get(key) : null; },
  setItem(key, value) { store.set(String(key), String(value)); },
  removeItem(key) { store.delete(key); },
};
class StorageEvent extends Event {
  constructor(type, init = {}) {
    super(type);
    this.key = init.key ?? null;
    this.oldValue = init.oldValue ?? null;
    this.newValue = init.newValue ?? null;
  }
}
globalThis.StorageEvent = StorageEvent;
globalThis.window = {
  addEventListener(type, fn) {
    const bucket = listeners.get(type) ?? new Set();
    bucket.add(fn);
    listeners.set(type, bucket);
  },
  removeEventListener(type, fn) {
    listeners.get(type)?.delete(fn);
  },
  dispatchEvent(event) {
    for (const fn of listeners.get(event.type) ?? []) fn(event);
    return true;
  },
};

const recorded = [];
let pending = [];
globalThis.fetch = (url, init = {}) => {
  const headers = init.headers ?? {};
  recorded.push({
    url: String(url),
    method: init.method ?? "GET",
    enterprise: headers["X-Enterprise-Id"] ?? null,
    authorization: headers.Authorization ?? null,
  });
  return new Promise((resolve, reject) => {
    pending.push({ resolve, reject, signal: init.signal });
  });
};

const apiUrl = pathToFileURL(process.env.API_TS).href;
const {
  commitTenantSnapshot,
  getTenantGeneration,
  getTenantSnapshot,
  setSelectedEnterprise,
  tenantFetch,
  ApiError,
} = await import(apiUrl);

function respondOk(index, body) {
  pending[index].resolve(new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  }));
}

const results = {};

{
  recorded.length = 0;
  pending = [];
  setSelectedEnterprise(A);
  commitTenantSnapshot(A, getTenantGeneration());
  const born = getTenantGeneration();
  let thenLanded = 0;
  let catchLanded = 0;
  let finallyLanded = 0;
  const slow = tenantFetch("/v1/analysis-reports/session/access", {
    token: "tok-a",
    parse: "json",
  }).then((payload) => {
    if (born !== getTenantGeneration()) return;
    thenLanded += 1;
    return payload;
  }).catch((error) => {
    if (born !== getTenantGeneration()) return;
    catchLanded += 1;
    throw error;
  }).finally(() => {
    if (born !== getTenantGeneration()) return;
    finallyLanded += 1;
  });
  setSelectedEnterprise(B);
  commitTenantSnapshot(B, getTenantGeneration());
  respondOk(0, { ok: true, from: "late-a" });
  await slow.catch(() => undefined);
  results.slow_switch = {
    thenLanded,
    catchLanded,
    finallyLanded,
    generationAdvanced: getTenantGeneration() > born,
    firstHeader: recorded[0]?.enterprise ?? null,
  };
}

{
  recorded.length = 0;
  pending = [];
  setSelectedEnterprise(A);
  commitTenantSnapshot(A, getTenantGeneration());
  const before = getTenantGeneration();
  localStorage.setItem(ENTERPRISE_KEY, B);
  setSelectedEnterprise(B);
  results.tamper_then_select_b = {
    generationBumped: getTenantGeneration() !== before,
    ready: getTenantSnapshot().ready,
    memory: getTenantSnapshot().enterpriseId,
  };
}

{
  recorded.length = 0;
  pending = [];
  setSelectedEnterprise(A);
  commitTenantSnapshot(A, getTenantGeneration());
  const before = getTenantGeneration();
  window.dispatchEvent(new StorageEvent("storage", {
    key: ENTERPRISE_KEY,
    oldValue: A,
    newValue: B,
  }));
  results.cross_tab_storage = {
    generationBumped: getTenantGeneration() !== before,
    ready: getTenantSnapshot().ready,
    memory: getTenantSnapshot().enterpriseId,
  };
}

{
  recorded.length = 0;
  pending = [];
  setSelectedEnterprise(A);
  commitTenantSnapshot(A, getTenantGeneration());
  localStorage.setItem(ENTERPRISE_KEY, UNKNOWN);
  const known = tenantFetch("/v1/analysis-reports/session/access", {
    token: "tok-a",
    parse: "json",
  });
  respondOk(pending.length - 1, { ok: true });
  await known;
  let unknownThrew = "";
  try {
    setSelectedEnterprise(UNKNOWN);
    await tenantFetch("/v1/analysis-reports/session/access", { token: "tok-b", parse: "json" });
  } catch (error) {
    unknownThrew = error instanceof ApiError ? error.code : String(error);
  }
  results.unknown_b = {
    knownHeader: recorded[0]?.enterprise ?? null,
    secondSent: recorded.length > 1,
    unknownThrew,
  };
}

{
  recorded.length = 0;
  pending = [];
  setSelectedEnterprise(null);
  let unready = "";
  try {
    await tenantFetch("/v1/analysis-reports/session/access", { token: "tok", parse: "json" });
  } catch (error) {
    unready = error instanceof ApiError ? error.code : String(error);
  }
  results.unready = { code: unready, fetchCount: recorded.length };
}

process.stdout.write(JSON.stringify(results) + "\n");
"""


class AnalysisReportStageBContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env = os.environ.copy()
        env["API_TS"] = str(WEB_SRC / "api.ts")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as handle:
            handle.write(HARNESS)
            script = handle.name
        try:
            completed = subprocess.run(
                [
                    "node",
                    "--experimental-strip-types",
                    "--disable-warning=ExperimentalWarning",
                    script,
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            os.unlink(script)
        if completed.returncode != 0:
            raise AssertionError(
                f"tenant behavior harness failed: {completed.stderr}\n{completed.stdout}"
            )
        cls.payload = json.loads(completed.stdout.splitlines()[-1])

    def test_slow_request_from_a_does_not_land_after_switch_to_b(self) -> None:
        payload = self.payload["slow_switch"]
        self.assertEqual(payload["thenLanded"], 0)
        self.assertEqual(payload["catchLanded"], 0)
        self.assertEqual(payload["finallyLanded"], 0)
        self.assertTrue(payload["generationAdvanced"])
        self.assertEqual(payload["firstHeader"], "20000000-0000-4000-8000-00000000000a")

    def test_same_page_storage_tamper_then_formal_select_b_invalidates(self) -> None:
        payload = self.payload["tamper_then_select_b"]
        self.assertTrue(payload["generationBumped"])
        self.assertFalse(payload["ready"])
        self.assertIsNone(payload["memory"])

    def test_cross_tab_storage_event_invalidates_snapshot(self) -> None:
        payload = self.payload["cross_tab_storage"]
        self.assertTrue(payload["generationBumped"])
        self.assertFalse(payload["ready"])
        self.assertIsNone(payload["memory"])

    def test_unknown_b_never_sends_enterprise_header(self) -> None:
        payload = self.payload["unknown_b"]
        self.assertEqual(payload["knownHeader"], "20000000-0000-4000-8000-00000000000a")
        self.assertFalse(payload["secondSent"])
        self.assertEqual(payload["unknownThrew"], "TENANT_SNAPSHOT_UNREADY")

    def test_unready_snapshot_does_not_fetch(self) -> None:
        payload = self.payload["unready"]
        self.assertEqual(payload["code"], "TENANT_SNAPSHOT_UNREADY")
        self.assertEqual(payload["fetchCount"], 0)


if __name__ == "__main__":
    unittest.main()
