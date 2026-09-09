"""Exercise cleanup against independent Docker resource inventories."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.f1 import analysis_report_postgres_integration as harness


class DockerInventory:
    def __init__(self, owner: harness.PostgresIntegrationStack) -> None:
        self.resources: dict[str, dict[str, dict[str, str]]] = {
            "container": {}, "volume": {}, "network": {}
        }
        self.removed: list[str] = []
        for kind, resources in self.resources.items():
            for label, project, project_id, scope in (
                ("own", owner.project_name, owner.project_id, harness.SCOPE),
                ("parallel", "anhuan-ar-pgint-other", "other-id", harness.SCOPE),
                ("reused-name", owner.project_name, "different-id", harness.SCOPE),
                ("foreign-scope", owner.project_name, owner.project_id, "other-scope"),
            ):
                resources[f"{label}-{kind}"] = {
                    "io.anhuan.scope": scope,
                    "com.docker.compose.project": project,
                    "io.anhuan.project-id": project_id,
                }

    def capture(self, command: list[str], **_kwargs: object) -> str:
        kind = "container" if command[1] == "ps" else command[1]
        filters: dict[str, str] = {}
        for index, value in enumerate(command[:-1]):
            if value == "--filter":
                key, expected = command[index + 1].removeprefix("label=").split("=", 1)
                filters[key] = expected
        return "\n".join(
            name for name, labels in self.resources[kind].items()
            if all(labels.get(key) == value for key, value in filters.items())
        )

    def run(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        if command[1] == "rm":
            kind, start = "container", 3
        else:
            kind = command[1]
            start = 4 if "-f" in command else 3
        for name in command[start:]:
            del self.resources[kind][name]
            self.removed.append(name)
        return subprocess.CompletedProcess(command, 0)


class AnalysisReportHarnessIsolationTests(unittest.TestCase):
    def test_partial_start_cleanup_preserves_parallel_and_mismatched_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stack = harness.PostgresIntegrationStack()
            stack.control_dir = Path(temporary) / "owned-run"
            stack.control_dir.mkdir()
            stack.before_fingerprint = b"shared-before"
            inventory = DockerInventory(stack)
            with (
                patch.object(harness.subprocess, "check_output", side_effect=inventory.capture),
                patch.object(harness.subprocess, "run", side_effect=inventory.run),
                patch.object(harness, "canonical_shared_fingerprint", return_value=b"shared-before"),
            ):
                self.assertEqual(
                    harness.dedicated_counts(project_name=stack.project_name, project_id=stack.project_id),
                    (1, 1, 1),
                )
                stack.stop()
                self.assertEqual(stack.dedicated_after, (0, 0, 0))
                self.assertEqual(stack.cleanup_status, "CLEAN")
                self.assertEqual(harness.dedicated_counts(), (2, 2, 2))
            self.assertCountEqual(inventory.removed, ["own-container", "own-volume", "own-network"])
            self.assertFalse(stack.control_dir.exists())
            for resources in inventory.resources.values():
                self.assertEqual(len(resources), 3)

    def test_incomplete_project_identity_is_rejected_before_any_docker_call(self) -> None:
        with patch.object(harness.subprocess, "check_output") as capture:
            for identity in ({"project_name": "one"}, {"project_id": "one"},
                             {"project_name": "", "project_id": ""}):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    harness.dedicated_counts(**identity)
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
