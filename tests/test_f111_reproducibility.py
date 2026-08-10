"""F1.1.1 reproducibility and immutable-v0.3 contracts.

These checks are deliberately offline.  No shared Docker project, fixed port,
database or fixture path is used.  Real no-cache builds, two clean rebuilds,
runtime image reconciliation and log/trace scans remain fixed formal gates;
this module verifies that those gates cannot be silently disconnected or
replaced by the legacy mutable v0.3 layout.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import tempfile
import unittest
from pathlib import Path

from infra.f1 import artifacts_v03
from infra.f1 import formal_acceptance
from infra.f1 import repro_verify


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/f1/docker-compose.yml"
REPAIR_COMPOSE = ROOT / "infra/f1/docker-compose.repair.yml"
API_DOCKER = ROOT / "infra/f1/Dockerfile"
WEB_DOCKER = ROOT / "infra/f1/web.Dockerfile"
PY_LOCK = ROOT / "requirements/requirements-f1.lock"
V02 = ROOT / "artifacts/f1-platform-shell/v0.2"
HEX_A = "a" * 64


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _service_blocks(text: str) -> dict[str, str]:
    service_text = text.split("services:\n", 1)[1]
    boundary = re.search(r"^[A-Za-z][A-Za-z0-9_-]*:\s*$", service_text, re.M)
    if boundary is not None:
        service_text = service_text[: boundary.start()]
    matches = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", service_text, re.M))
    return {
        match.group(1): service_text[
            match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(service_text)
        ]
        for index, match in enumerate(matches)
    }


def _machine_evidence() -> dict[str, object]:
    gates: dict[str, dict[str, object]] = {
        name: {"exit": 0, "normalized_output_sha256": HEX_A}
        for name in artifacts_v03.REQUIRED_GATES
    }
    gates["reverse"]["metrics"] = {
        name: 0 for name in artifacts_v03.REVERSE_METRICS
    }
    gates["clean_rebuild_1"]["result_sha256"] = HEX_A
    gates["clean_rebuild_2"]["result_sha256"] = HEX_A
    gates["sbom_reconcile"]["inventory_sha256"] = artifacts_v03.inventory_digest(ROOT)
    return {"schema": artifacts_v03.EVIDENCE_SCHEMA, "gates": gates}


def _publish_diagnostic(base: Path) -> tuple[dict, Path]:
    evidence = base / "evidence.json"
    evidence.write_text(
        json.dumps(_machine_evidence(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    evidence.chmod(0o600)
    output = base / "v0.3"
    result = artifacts_v03.publish(
        root=ROOT, evidence_path=evidence, output_dir=output
    )
    if result.exit_code != 2:
        raise AssertionError("serialized diagnostic evidence unexpectedly completed")
    current = json.loads((output / "current.json").read_text(encoding="utf-8"))
    return current, output / "batches" / current["batch_id"]


class ImagePinningTests(unittest.TestCase):
    def test_all_third_party_images_pinned_to_sha256(self) -> None:
        components = artifacts_v03._compose_components(ROOT)
        self.assertEqual(
            {component["name"] for component in components},
            set(_service_blocks(COMPOSE.read_text(encoding="utf-8"))),
        )
        for component in components:
            reference = component["properties"][0]["value"]
            if str(reference).startswith("anhuan-f1-"):
                continue
            self.assertRegex(str(reference), r"@sha256:[0-9a-f]{64}$")
            self.assertEqual(len(component.get("hashes", [])), 1)

    def test_no_unpinned_latest_or_nightly_tags(self) -> None:
        for component in artifacts_v03._compose_components(ROOT):
            reference = str(component["properties"][0]["value"])
            if reference.startswith("anhuan-f1-"):
                continue
            self.assertIn("@sha256:", reference)
            tag = reference.split("@", 1)[0].rsplit(":", 1)[-1].lower()
            self.assertNotIn(tag, {"latest", "nightly"})

    def test_api_worker_web_have_build_blocks(self) -> None:
        blocks = _service_blocks(COMPOSE.read_text(encoding="utf-8"))
        for service in ("api", "worker", "web"):
            self.assertIn(service, blocks)
            self.assertIn("build:", blocks[service])
            self.assertIn("dockerfile:", blocks[service])
        override = REPAIR_COMPOSE.read_text(encoding="utf-8")
        for prefix in (
            "anhuan-f111-repair-api:",
            "anhuan-f111-repair-worker:",
            "anhuan-f111-repair-web:",
        ):
            self.assertIn(prefix, override)

    def test_web_has_no_dist_bind_mount(self) -> None:
        web = _service_blocks(COMPOSE.read_text(encoding="utf-8"))["web"]
        self.assertNotIn("/app/dist:", web)
        self.assertNotIn("./dist", web)
        self.assertIn("COPY --from=build /app/dist", WEB_DOCKER.read_text(encoding="utf-8"))

    def test_dockerfile_bases_pinned_to_sha256(self) -> None:
        components = artifacts_v03._dockerfile_components(ROOT)
        self.assertEqual(len(components), 3)
        for component in components:
            reference = str(component["properties"][0]["value"])
            self.assertRegex(reference, r"@sha256:[0-9a-f]{64}$")
            self.assertEqual(len(component.get("hashes", [])), 1)


class LockReproducibilityTests(unittest.TestCase):
    def test_python_lock_every_line_has_hash(self) -> None:
        meaningful = [
            line for line in PY_LOCK.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        components = artifacts_v03._python_components(ROOT)
        self.assertGreaterEqual(len(meaningful), 40)
        self.assertEqual(len(components), len(meaningful))
        self.assertTrue(all(component.get("hashes") for component in components))

    def test_python_lock_has_version(self) -> None:
        for line in PY_LOCK.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^\s]+")

    def test_python_lock_installs_in_docker_base(self) -> None:
        # The live proof is the fixed no-cache Compose build in both clean
        # rebuild rounds; this offline test prevents substituting a host pip.
        clean = _source("tests/f111_clean_rebuild.py")
        self.assertIn('"build",', clean)
        self.assertIn('"--no-cache",', clean)
        self.assertIn('"api",\n                "worker",\n                "web",', clean)
        self.assertIn("requirements/requirements-f1.lock", clean)
        self.assertEqual(
            sum(spec.name.startswith("clean_rebuild_") for spec in formal_acceptance.COMMAND_REGISTRY),
            2,
        )

    def test_runtime_image_enforces_hash_mode(self) -> None:
        dockerfile = API_DOCKER.read_text(encoding="utf-8")
        self.assertIn("pip install --require-hashes --no-cache-dir", dockerfile)
        self.assertIn("COPY requirements/requirements-f1.lock", dockerfile)


class RAGFlowLogTests(unittest.TestCase):
    def test_ragflow_logs_not_bound_to_repo(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("./ragflow/logs", compose)
        self.assertNotIn("ragflow/logs:/ragflow/logs", compose)
        self.assertIn("ragflow_logs:/ragflow/logs", compose)

    def test_repo_ragflow_logs_dir_is_empty(self) -> None:
        logs = ROOT / "infra/f1/ragflow/logs"
        if logs.exists():
            self.assertEqual([path for path in logs.rglob("*") if path.is_file()], [])
        canary = _source("tests/f111_log_canary.py")
        self.assertIn('service == "ragflow"', canary)
        self.assertIn('destination == "/ragflow/logs"', canary)
        self.assertIn("scan_container_surfaces", canary)


class CleanRebuildSmokeTests(unittest.TestCase):
    def test_git_tracked_only_clean_checkout(self) -> None:
        clean = _source("tests/f111_clean_rebuild.py")
        for marker in (
            '"git", "ls-files", "-z", "--cached"',
            '"git", "ls-files", "-z", "--others", "--exclude-standard"',
            "untracked_delivery_allowed(relative)",
            "SOURCE_DRIFT",
            'scratch.parent != Path("/private/tmp")',
        ):
            self.assertIn(marker, clean)
        self.assertNotIn("git clone", clean)

    def test_compose_no_shared_volume_binds_secrets(self) -> None:
        blocks = _service_blocks(COMPOSE.read_text(encoding="utf-8"))
        for service in ("api", "worker", "dispatcher"):
            block = blocks[service]
            self.assertNotIn("f1_bootstrap_password", block)
            self.assertNotIn("f1_migration_dsn", block)
            for line in block.splitlines():
                if ":/run/secrets/" in line and line.lstrip().startswith("-"):
                    self.assertTrue(line.rstrip().endswith(":ro"), line.strip())
        self.assertIn(
            "${F1_SECRETS_DIR:?F1_SECRETS_DIR_REQUIRED}",
            COMPOSE.read_text(encoding="utf-8"),
        )


class LogCanaryTests(unittest.TestCase):
    def test_no_repo_log_contains_canary_markers(self) -> None:
        canary = _source("tests/f111_log_canary.py")
        for marker in (
            "logs\", \"--no-color",
            "scan_http_surfaces()",
            "scan_container_surfaces(scope)",
            "scan_host_tree(PUBLIC_ARTIFACT_ROOT",
            'print(f"F111_LOG_CANARY_HITS={hits}")',
        ):
            self.assertIn(marker, canary)
        self.assertNotIn("print(error", canary)
        self.assertNotIn("print(config", canary)


class V0_3ArtifactTests(unittest.TestCase):
    def test_v02_revocation_exists(self) -> None:
        revocation = V02 / "revocation.json"
        self.assertTrue(revocation.is_file())
        data = json.loads(revocation.read_text(encoding="utf-8"))
        self.assertTrue(data.get("revoked"))
        self.assertIn("replaced_by", data)

    def test_v03_acceptance_gates_bound_to_stdout_sha(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anhuan-f111-v03-test-") as raw:
            current, batch = _publish_diagnostic(Path(raw))
            self.assertEqual(current["schema"], "f1.1.1-current-batch-v1")
            self.assertEqual(batch.name, current["batch_id"])
            self.assertEqual(stat.S_IMODE(batch.stat().st_mode), 0o700)
            self.assertFalse((batch.parent.parent / "acceptance.json").exists())
            for name, digest in current["files"].items():
                target = batch / name
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            acceptance = json.loads((batch / "acceptance.json").read_text(encoding="utf-8"))
            self.assertEqual(set(acceptance["gates"]), set(artifacts_v03.REQUIRED_GATES))
            for gate in acceptance["gates"].values():
                self.assertRegex(gate["normalized_output_sha256"], r"^[0-9a-f]{64}$")

    def test_v03_reverse_line_is_real_output(self) -> None:
        self.assertEqual(len(artifacts_v03.REVERSE_METRICS), 20)
        self.assertEqual(
            tuple(artifacts_v03.REVERSE_METRICS),
            tuple(__import__("tests.f111_reverse_verify", fromlist=["METRICS"]).METRICS),
        )
        line = " ".join(f"{name}=0" for name in artifacts_v03.REVERSE_METRICS)
        self.assertEqual(repro_verify.parse_reverse_metrics(line), dict.fromkeys(artifacts_v03.REVERSE_METRICS, 0))
        with tempfile.TemporaryDirectory(prefix="anhuan-f111-v03-test-") as raw:
            _current, batch = _publish_diagnostic(Path(raw))
            acceptance = json.loads((batch / "acceptance.json").read_text(encoding="utf-8"))
            reverse = acceptance["gates"]["reverse"]
            self.assertEqual(reverse["metric_count"], 20)
            self.assertEqual(reverse["metric_nonzero"], 0)
            self.assertNotIn("reverse_verify_line", acceptance)

    def test_v03_sbom_valid_cyclonedx(self) -> None:
        components = artifacts_v03.build_inventory(ROOT)
        service_names = {
            str(component["bom-ref"]).removeprefix("compose:")
            for component in components
            if str(component["bom-ref"]).startswith("compose:")
        }
        self.assertEqual(service_names, set(formal_acceptance.RUNTIME_SERVICES))
        service_hashes = {
            name: hashlib.sha256(("service:" + name).encode()).hexdigest()
            for name in service_names
        }
        base_refs = {
            str(component["bom-ref"])
            for component in components
            if str(component["bom-ref"]).startswith("dockerfile:")
        }
        base_hashes = {
            ref: hashlib.sha256(("base:" + ref).encode()).hexdigest()
            for ref in base_refs
        }
        runtime_sha = hashlib.sha256(b"runtime-inventory").hexdigest()
        runtime = {
            "services": [
                {"service": name, "image_sha256": digest}
                for name, digest in sorted(service_hashes.items())
            ],
            "bases": [
                {"bom_ref": ref, "image_sha256": digest}
                for ref, digest in sorted(base_hashes.items())
            ],
            "build_inputs": {
                name: hashlib.sha256(("build:" + name).encode()).hexdigest()
                for name in formal_acceptance.clean_rebuild.BUILD_PROVENANCE_LABELS
            },
            "runtime_inventory_sha256": runtime_sha,
        }
        enriched = formal_acceptance._runtime_components(components, runtime)
        sbom = artifacts_v03._sbom(enriched, runtime_sha)
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        by_ref = {component["bom-ref"]: component for component in sbom["components"]}
        for name, digest in service_hashes.items():
            component = by_ref[f"compose:{name}"]
            self.assertEqual(component["hashes"], [{"alg": "SHA-256", "content": digest}])
            self.assertIn(
                {"name": "oci:runtime-image-id-sha256", "value": digest},
                component["properties"],
            )
        for ref, digest in base_hashes.items():
            self.assertEqual(by_ref[ref]["hashes"][0]["content"], digest)
        self.assertTrue(any(ref.startswith("pkg:pypi/") for ref in by_ref))
        self.assertTrue(any(ref.startswith("pkg:npm/") for ref in by_ref))


if __name__ == "__main__":
    unittest.main()
