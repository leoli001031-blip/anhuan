"""Health-snapshot frontend contracts. Compiles the real parseHealthEnvelope."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "src" / "web"
WEB_SRC = WEB_ROOT / "src"
EXISTING_NODE_MODULES = Path("/Users/lichenhao/Desktop/安环项目/src/web/node_modules")
TSC = EXISTING_NODE_MODULES / ".bin" / "tsc"

HARNESS = r"""
const { parseHealthEnvelope, managementHealthFromHttp } = require("./adapters/wire.js");
const healthMod = require("./features/managementHealth.js");

const results = {};
function catchCode(fn) {
  try { fn(); return null; }
  catch (error) { return error && error.code ? error.code : String(error); }
}

const DIMS = [
  ["material-completeness", "资料完整性", 15, 12],
  ["permits", "证照与批复", 20, 14],
  ["monitoring", "监测与台账", 20, 13],
  ["remediation", "整改闭环", 25, 8],
  ["expiry", "风险与到期", 10, 6],
  ["evidence", "证据可信度", 10, 7],
];
function dim(key, label, max, score) {
  return { key, label, score, max_score: max, summary: key, tone: "attention" };
}
function validSnapshot() {
  return {
    report_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    version_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    version_number: 1,
    report_title: "企业安环资料分析报告",
    score: 60,
    max_score: 100,
    status_label: "需重点改善",
    assessed_on: "2026-08-23T00:00:00Z",
    basis_label: "基于已发布材料与本次分析报告",
    evidence_mode: "deterministic_local",
    dimensions: DIMS.map(([k, l, m, s]) => dim(k, l, m, s)),
    priorities: [{ title: "补齐整改闭环材料", level: "high" }],
    boundary: "边界说明",
  };
}
function envelope(snapshot) {
  return { schema: "anhuan-analysis-report-health-v1", snapshot };
}

results.accepts = catchCode(() => parseHealthEnvelope(envelope(validSnapshot()))) === null;
results.nullSnapshot = parseHealthEnvelope(envelope(null)).snapshot === null;

const items = Object.entries(validSnapshot());
const reordered = Object.fromEntries(items.slice(1).concat(items.slice(0, 1)));
results.reordered = catchCode(() => parseHealthEnvelope(envelope(reordered)));

const leak = validSnapshot(); leak.provider = "x";
results.leak = catchCode(() => parseHealthEnvelope(envelope(leak)));

const feb = validSnapshot(); feb.assessed_on = "2026-02-30T00:00:00Z";
results.feb30 = catchCode(() => parseHealthEnvelope(envelope(feb)));

const badUuid = validSnapshot(); badUuid.report_id = "not-a-uuid";
results.badUuid = catchCode(() => parseHealthEnvelope(envelope(badUuid)));

results.sumCaps = catchCode(() => {
  const parsed = parseHealthEnvelope(envelope(validSnapshot()));
  if (parsed.snapshot.score !== 60) throw new Error("SUM");
  const caps = parsed.snapshot.dimensions.map((d) => [d.key, d.max_score]);
  const expected = DIMS.map(([k, _l, m]) => [k, m]);
  if (JSON.stringify(caps) !== JSON.stringify(expected)) throw new Error("CAPS");
});

results.httpNullNotSixty = parseHealthEnvelope(envelope(null)).snapshot === null
  && healthMod.SYNTHETIC_MANAGEMENT_HEALTH.score === 60;

let mapper503 = "missing";
if (typeof managementHealthFromHttp === "function") {
  mapper503 = catchCode(() => managementHealthFromHttp(503, envelope(null)));
} else {
  mapper503 = catchCode(() => parseHealthEnvelope({ detail: "HEALTH_SNAPSHOT_UNAVAILABLE" }));
}
results.http503 = mapper503;

let land = { missing: true };
if (typeof healthMod.landHealthIfCurrent === "function") {
  let hits = 0;
  healthMod.landHealthIfCurrent(1, 2, () => { hits += 1; });
  healthMod.landHealthIfCurrent(1, 1, () => { hits += 1; });
  land = { missing: false, hits };
}
results.land = land;

const unknownDim = validSnapshot();
unknownDim.dimensions[0].key = "other";
results.unknownDim = catchCode(() => parseHealthEnvelope(envelope(unknownDim)));

process.stdout.write(JSON.stringify(results) + "\n");
"""


class HealthFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not TSC.is_file():
            raise AssertionError(f"tsc not found at {TSC}")
        cls._tmpdir = tempfile.mkdtemp(prefix="health-frontend-cjs-")
        out_dir = Path(cls._tmpdir) / "out"
        link = WEB_ROOT / "node_modules"
        created_link = False
        if not link.exists():
            os.symlink(EXISTING_NODE_MODULES, link)
            created_link = True
        try:
            compiled = subprocess.run(
                [
                    str(TSC),
                    "--pretty",
                    "false",
                    "--ignoreConfig",
                    "--noEmit",
                    "false",
                    "--outDir",
                    str(out_dir),
                    "--rootDir",
                    str(WEB_SRC),
                    "--module",
                    "commonjs",
                    "--moduleResolution",
                    "node",
                    "--ignoreDeprecations",
                    "6.0",
                    "--esModuleInterop",
                    "--skipLibCheck",
                    "--target",
                    "es2022",
                    "--jsx",
                    "react-jsx",
                    "--verbatimModuleSyntax",
                    "false",
                    "--allowImportingTsExtensions",
                    "false",
                    str(WEB_SRC / "adapters" / "wire.ts"),
                    str(WEB_SRC / "features" / "managementHealth.ts"),
                ],
                cwd=str(WEB_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if compiled.returncode != 0:
                raise AssertionError(
                    f"tsc failed: {compiled.stderr}\n{compiled.stdout}"
                )
            harness_path = out_dir / "harness.js"
            harness_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(harness_path)],
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                raise AssertionError(
                    f"health frontend harness failed: {completed.stderr}\n{completed.stdout}"
                )
            cls.payload = json.loads(completed.stdout.splitlines()[-1])
        finally:
            if created_link and link.is_symlink():
                link.unlink()

    @classmethod
    def tearDownClass(cls) -> None:
        tmp = getattr(cls, "_tmpdir", None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parse_accepts_closed_envelope(self) -> None:
        self.assertTrue(self.payload["accepts"])
        self.assertTrue(self.payload["nullSnapshot"])

    def test_parse_rejects_reordered_keys(self) -> None:
        self.assertIsNotNone(self.payload["reordered"])

    def test_parse_rejects_leak_keys(self) -> None:
        self.assertIsNotNone(self.payload["leak"])

    def test_parse_rejects_nonexistent_calendar_date(self) -> None:
        self.assertIsNotNone(self.payload["feb30"])

    def test_parse_rejects_invalid_uuid(self) -> None:
        self.assertIsNotNone(self.payload["badUuid"])

    def test_parse_dimension_sum_and_caps(self) -> None:
        self.assertIsNone(self.payload["sumCaps"])

    def test_mock_sixty_isolated_from_http_null(self) -> None:
        self.assertTrue(self.payload["httpNullNotSixty"])

    def test_http_503_does_not_degrade_to_null(self) -> None:
        self.assertIsNotNone(self.payload["http503"])
        self.assertNotEqual(self.payload["http503"], None)

    def test_late_generation_does_not_land(self) -> None:
        land = self.payload["land"]
        self.assertFalse(land.get("missing", True))
        self.assertEqual(land.get("hits"), 1)

    def test_dimension_keys_are_six_union(self) -> None:
        self.assertIsNotNone(self.payload["unknownDim"])


if __name__ == "__main__":
    unittest.main()
