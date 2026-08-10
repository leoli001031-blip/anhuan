"""Anti-fake-green contract tests for the F1.1.1 v0.3 publisher.

All tests use a temporary repository and machine-evidence bundle.  They never
start or mutate the shared F1 services and they never write the public v0.3
directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from infra.f1 import artifacts_v03
from infra.f1 import repro_verify


HEX_A = "a" * 64
HEX_B = "b" * 64


class ArtifactPublisherContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="anhuan-f111-artifact-test-")
        self.root = Path(self.temp.name) / "repo"
        (self.root / "infra/f1").mkdir(parents=True)
        (self.root / "requirements").mkdir()
        (self.root / "src/web").mkdir(parents=True)
        (self.root / "infra/f1/docker-compose.yml").write_text(
            "services:\n"
            "  redis:\n"
            f"    image: redis:7@sha256:{HEX_A}\n"
            "  api:\n"
            "    image: anhuan-f1-api:test\n"
            "    build:\n      context: ../..\n      dockerfile: infra/f1/Dockerfile\n",
            encoding="utf-8",
        )
        (self.root / "infra/f1/Dockerfile").write_text(
            f"FROM python:3.11@sha256:{HEX_B}\n", encoding="utf-8"
        )
        (self.root / "infra/f1/web.Dockerfile").write_text(
            f"FROM node:22@sha256:{HEX_A} AS build\nFROM nginx:1@sha256:{HEX_B}\n",
            encoding="utf-8",
        )
        (self.root / "requirements/requirements-f1.lock").write_text(
            f"alpha==1.2.3 --hash=sha256:{HEX_A}\n"
            f"bravo==4.5.6 --hash=sha256:{HEX_B}\n",
            encoding="utf-8",
        )
        (self.root / "src/web/package-lock.json").write_text(
            json.dumps(
                {
                    "name": "web",
                    "lockfileVersion": 3,
                    "packages": {
                        "": {"name": "web", "version": "0.0.0"},
                        "node_modules/react": {
                            "version": "19.2.0",
                            "integrity": "sha512-YWJj",
                        },
                        "node_modules/@scope/tool": {
                            "version": "2.0.0",
                            "integrity": "sha256-YWJj",
                        },
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.output = Path(self.temp.name) / "published"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _evidence(self, **gate_overrides: dict) -> dict:
        gates = {}
        for name in artifacts_v03.REQUIRED_GATES:
            gates[name] = {"exit": 0, "normalized_output_sha256": HEX_A}
        gates["reverse"]["metrics"] = {
            name: 0 for name in artifacts_v03.REVERSE_METRICS
        }
        gates["clean_rebuild_1"]["result_sha256"] = HEX_B
        gates["clean_rebuild_2"]["result_sha256"] = HEX_B
        gates["sbom_reconcile"]["inventory_sha256"] = artifacts_v03.inventory_digest(
            self.root
        )
        for name, override in gate_overrides.items():
            gates[name].update(override)
        return {"schema": artifacts_v03.EVIDENCE_SCHEMA, "gates": gates}

    def _write_evidence(self, data: dict) -> Path:
        path = Path(self.temp.name) / "evidence.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _publish(self, data: dict):
        return artifacts_v03.publish(
            root=self.root,
            evidence_path=self._write_evidence(data),
            output_dir=self.output,
        )

    def _batch_files(self) -> tuple[dict, Path]:
        current = json.loads((self.output / "current.json").read_text(encoding="utf-8"))
        batch = self.output / "batches" / current["batch_id"]
        return current, batch

    def test_required_gate_set_covers_every_acceptance_family(self) -> None:
        self.assertEqual(
            set(artifacts_v03.REQUIRED_GATES),
            {
                "migration_replay", "targeted_tests", "full_repository_tests",
                "npm_ci", "npm_lint", "npm_build", "reverse",
                "clean_rebuild_1", "clean_rebuild_2", "log_canary",
                "sbom_reconcile",
            },
        )

    def test_missing_gate_rejects_and_failed_batch_has_no_ready_token(self) -> None:
        evidence = self._evidence()
        evidence["gates"].pop("npm_build")
        result = self._publish(evidence)
        self.assertEqual(result.exit_code, 2)
        _current, batch = self._batch_files()
        joined = b"".join(p.read_bytes() for p in batch.iterdir() if p.is_file())
        self.assertNotIn(b"READY", joined)

    def test_nonzero_gate_rejects(self) -> None:
        result = self._publish(self._evidence(targeted_tests={"exit": 3}))
        self.assertEqual(result.exit_code, 2)
        _current, batch = self._batch_files()
        data = json.loads((batch / "acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(data["conclusion"], artifacts_v03.REJECTED_CONCLUSION)

    def test_reverse_metric_nonzero_rejects(self) -> None:
        evidence = self._evidence()
        evidence["gates"]["reverse"]["metrics"]["stale_lease_commit"] = 1
        self.assertEqual(self._publish(evidence).exit_code, 2)

    def test_reverse_metric_missing_rejects(self) -> None:
        evidence = self._evidence()
        evidence["gates"]["reverse"]["metrics"].pop("audit_gaps")
        self.assertEqual(self._publish(evidence).exit_code, 2)

    def test_clean_rebuild_result_mismatch_rejects(self) -> None:
        self.assertEqual(
            self._publish(
                self._evidence(clean_rebuild_2={"result_sha256": HEX_A})
            ).exit_code,
            2,
        )

    def test_sbom_inventory_mismatch_rejects(self) -> None:
        self.assertEqual(
            self._publish(
                self._evidence(sbom_reconcile={"inventory_sha256": HEX_B})
            ).exit_code,
            2,
        )

    def test_raw_stdout_or_stderr_is_rejected_not_persisted(self) -> None:
        evidence = self._evidence()
        evidence["gates"]["targeted_tests"]["stdout"] = "PRIVATE BODY"
        result = self._publish(evidence)
        self.assertEqual(result.exit_code, 2)
        _current, batch = self._batch_files()
        joined = b"".join(p.read_bytes() for p in batch.iterdir() if p.is_file())
        self.assertNotIn(b"PRIVATE BODY", joined)

    def test_absolute_path_in_evidence_is_rejected_not_persisted(self) -> None:
        evidence = self._evidence()
        evidence["gates"]["npm_ci"]["cwd"] = "/private/tmp/private-repo"
        result = self._publish(evidence)
        self.assertEqual(result.exit_code, 2)
        _current, batch = self._batch_files()
        joined = b"".join(p.read_bytes() for p in batch.iterdir() if p.is_file())
        self.assertNotIn(b"/private/tmp", joined)

    def test_fabricated_all_green_serialized_evidence_is_noncompletable(self) -> None:
        result = self._publish(self._evidence())
        self.assertEqual(result.exit_code, 2)
        _current, batch = self._batch_files()
        data = json.loads((batch / "acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(data["conclusion"], artifacts_v03.REJECTED_CONCLUSION)
        self.assertFalse(data["accepted"])
        self.assertIn("FORMAL_ORCHESTRATOR_REQUIRED", data["blockers"])
        self.assertNotIn(b"READY", (batch / "acceptance.json").read_bytes())
        self.assertFalse(data["production"])
        self.assertFalse(data["accuracy_evaluated"])

    def test_staging_and_batch_permissions_are_private(self) -> None:
        self._publish(self._evidence())
        current, batch = self._batch_files()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.output / ".staging").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(batch.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.output / "current.json").stat().st_mode), 0o600)
        for name in current["files"]:
            self.assertEqual(stat.S_IMODE((batch / name).stat().st_mode), 0o600)

    def test_current_manifest_hashes_every_immutable_batch_file(self) -> None:
        self._publish(self._evidence())
        current, batch = self._batch_files()
        for name, expected in current["files"].items():
            self.assertEqual(hashlib.sha256((batch / name).read_bytes()).hexdigest(), expected)
        self.assertFalse(any(self.output.joinpath(".staging").iterdir()))

    def test_second_publish_is_byte_identical_and_reuses_batch(self) -> None:
        first = self._publish(self._evidence())
        before = (self.output / "current.json").read_bytes()
        second = self._publish(self._evidence())
        after = (self.output / "current.json").read_bytes()
        self.assertEqual(first.batch_id, second.batch_id)
        self.assertEqual(before, after)
        self.assertEqual(len(list((self.output / "batches").iterdir())), 1)

    def test_publish_retires_only_legacy_mutable_top_level_snapshots(self) -> None:
        self.output.mkdir(mode=0o700)
        for name in ("acceptance.json", "status.html", "sbom.json"):
            (self.output / name).write_text("OLD_READY", encoding="utf-8")
        unrelated = self.output / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        self._publish(self._evidence())
        for name in ("acceptance.json", "status.html", "sbom.json"):
            self.assertFalse((self.output / name).exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_existing_batch_tamper_fails_closed_without_overwrite(self) -> None:
        first = self._publish(self._evidence())
        _current, batch = self._batch_files()
        target = batch / "status.html"
        target.chmod(0o600)
        target.write_text("tampered", encoding="utf-8")
        target.chmod(0o400)
        with self.assertRaises(artifacts_v03.ImmutableBatchError):
            self._publish(self._evidence())
        self.assertEqual(target.read_text(encoding="utf-8"), "tampered")
        self.assertEqual(first.batch_id, batch.name)

    def test_sbom_covers_compose_dockerfile_python_and_npm_inventory(self) -> None:
        refs = {
            component["bom-ref"]
            for component in artifacts_v03.build_inventory(self.root)
        }
        self.assertIn("compose:redis", refs)
        self.assertIn("dockerfile:api:python:3.11", refs)
        self.assertIn("pkg:pypi/alpha@1.2.3", refs)
        self.assertIn("pkg:npm/react@19.2.0", refs)
        self.assertIn("pkg:npm/%40scope/tool@2.0.0", refs)

    def test_inventory_digest_changes_when_actual_compose_image_changes(self) -> None:
        before = artifacts_v03.inventory_digest(self.root)
        compose = self.root / "infra/f1/docker-compose.yml"
        compose.write_text(compose.read_text().replace(HEX_A, HEX_B), encoding="utf-8")
        after = artifacts_v03.inventory_digest(self.root)
        self.assertNotEqual(before, after)

    def test_repro_normalization_removes_path_time_uuid_and_ansi(self) -> None:
        first = (
            f"\x1b[31m{self.root}/tests 2026-08-09T11:00:00Z "
            "123e4567-e89b-42d3-a456-426614174000 elapsed=1.20s\x1b[0m\n"
        )
        second = (
            f"{self.root}/tests 2026-08-09T11:01:00Z "
            "123e4567-e89b-42d3-a456-426614174001 elapsed=9.80s\n"
        )
        self.assertEqual(
            repro_verify.normalized_digest(first, self.root),
            repro_verify.normalized_digest(second, self.root),
        )

    def test_repro_reverse_parser_requires_exact_twenty_metrics(self) -> None:
        line = " ".join(f"{name}=0" for name in artifacts_v03.REVERSE_METRICS)
        self.assertEqual(len(repro_verify.parse_reverse_metrics(line)), 20)
        with self.assertRaises(repro_verify.ReverseEvidenceError):
            repro_verify.parse_reverse_metrics(line.replace(" audit_gaps=0", ""))

    def test_repro_gate_record_never_persists_command_or_output(self) -> None:
        evidence = Path(self.temp.name) / "machine/evidence.json"
        gate = repro_verify.record_gate(
            evidence_path=evidence,
            name="targeted_tests",
            command=[os.sys.executable, "-c", "print('PRIVATE-CANARY')"],
            root=self.root,
        )
        self.assertEqual(gate["exit"], 0)
        raw = evidence.read_bytes()
        self.assertNotIn(b"PRIVATE-CANARY", raw)
        self.assertNotIn(str(self.root).encode(), raw)
        self.assertNotIn(b"command", raw)
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(evidence.parent.stat().st_mode), 0o700)

    def test_repro_sbom_gate_computes_inventory_without_external_command(self) -> None:
        evidence = Path(self.temp.name) / "machine/evidence.json"
        gate = repro_verify.record_gate(
            evidence_path=evidence,
            name="sbom_reconcile",
            command=[],
            root=self.root,
        )
        self.assertEqual(gate["exit"], 0)
        self.assertEqual(gate["inventory_sha256"], artifacts_v03.inventory_digest(self.root))


if __name__ == "__main__":
    unittest.main()
