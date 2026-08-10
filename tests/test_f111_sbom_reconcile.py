"""Offline anti-fake contracts for the fixed runtime SBOM reconciler."""
from __future__ import annotations

import inspect
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import f111_sbom_reconcile as reconcile


class F111SbomReconcileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = inspect.getsource(reconcile)

    @staticmethod
    def _provenance() -> dict[str, str]:
        return {
            name: format(index + 700, "064x")
            for index, name in enumerate(
                sorted(reconcile.clean_rebuild.BUILD_PROVENANCE_LABELS)
            )
        }

    def test_project_scope_is_exact_uuid4(self) -> None:
        self.assertTrue(
            reconcile._valid_project(
                "anhuan-f111-repair-0123456789ab4def8fedcba987654321"
            )
        )
        for value in (
            "anhuan-f111-repair-short",
            "anhuan-f111-repair-0123456789abcdef0123456789abcdef",
            "other-0123456789ab4def8fedcba987654321",
        ):
            self.assertFalse(reconcile._valid_project(value), value)

    def test_fixed_compose_files_and_no_caller_commands(self) -> None:
        self.assertIn("docker-compose.yml", self.source)
        self.assertIn("docker-compose.repair.yml", self.source)
        self.assertNotIn("shell=True", self.source)
        self.assertNotIn("argparse", self.source)
        self.assertNotIn("os.system", self.source)

    def test_every_runtime_service_and_container_is_reconciled(self) -> None:
        for token in (
            "config",
            "--format",
            "json",
            "ps",
            "--all",
            "docker",
            "inspect",
            "com.docker.compose.project",
            "com.docker.compose.service",
        ):
            self.assertIn(token, self.source)

    def test_pinned_and_local_image_contracts_are_distinct(self) -> None:
        for token in (
            "@sha256:",
            "anhuan-f111-repair-api:",
            "anhuan-f111-repair-worker:",
            "anhuan-f111-repair-web:",
            "RepoDigests",
            "RepoTags",
            "runtime_inventory_sha256",
        ):
            self.assertIn(token, self.source)

    def test_actual_inventory_uses_compose_dockerfiles_and_both_locks(self) -> None:
        for token in (
            "artifacts.build_inventory",
            "artifacts.inventory_digest",
            "requirements-f1.lock",
            "package-lock.json",
            "web.Dockerfile",
        ):
            self.assertIn(token, self.source)

    def test_failure_is_body_free_and_success_marker_is_unique(self) -> None:
        self.assertEqual(
            self.source.count("F111_RUNTIME_INVENTORY_SHA256="), 1
        )
        self.assertNotIn("traceback", self.source.lower())
        self.assertNotIn("print(error", self.source.lower())
        self.assertNotIn("print(exc", self.source.lower())

    def test_command_failure_and_output_limit_are_fail_closed(self) -> None:
        self.assertIn("COMMAND_FAILED", self.source)
        self.assertIn("OUTPUT_LIMIT", self.source)
        self.assertIn("timeout=", self.source)
        self.assertIn("DOCKER_SHA256", self.source)
        self.assertIn("LOCAL_UNIX_SOCKET_TRUST_BASE", self.source)

    def test_real_tracked_inventory_is_complete_and_deterministic(self) -> None:
        first = reconcile._static_inventory()
        second = reconcile._static_inventory()
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, second)

    def test_service_image_contract_rejects_missing_unpinned_and_wrong_local(self) -> None:
        project = "anhuan-f111-repair-0123456789ab4def8fedcba987654321"
        local = reconcile._local_images(project)
        services = {
            name: {
                "image": local.get(name, f"registry.invalid/{name}@sha256:{'a' * 64}")
            }
            for name in reconcile.EXPECTED_SERVICES
        }
        model = {"services": services}
        self.assertEqual(
            set(reconcile._service_images(model, project)),
            set(reconcile.EXPECTED_SERVICES),
        )
        del services["redis"]
        with self.assertRaises(reconcile.ReconcileError):
            reconcile._service_images(model, project)
        services["redis"] = {"image": "redis:latest"}
        with self.assertRaises(reconcile.ReconcileError):
            reconcile._service_images(model, project)
        services["redis"] = {"image": f"redis@sha256:{'b' * 64}"}
        services["api"] = {"image": "anhuan-f1-api:f111"}
        with self.assertRaises(reconcile.ReconcileError):
            reconcile._service_images(model, project)

    def test_compose_ps_requires_exact_healthy_runtime(self) -> None:
        rows = []
        for name in sorted(reconcile.EXPECTED_SERVICES):
            rows.append(
                {
                    "Service": name,
                    "ID": name + "-container",
                    "State": "exited" if name == "keycloak-provisioner" else "running",
                    "Health": "" if name == "keycloak-provisioner" else "healthy",
                    "ExitCode": 0,
                }
            )
        result = reconcile.CommandResult(json.dumps(rows).encode(), b"")
        with mock.patch.object(reconcile, "_run_command", return_value=result):
            parsed = reconcile._compose_ps(
                "anhuan-f111-repair-0123456789ab4def8fedcba987654321", 60
            )
        self.assertEqual(set(parsed), set(reconcile.EXPECTED_SERVICES))
        rows[0]["Health"] = "starting"
        rows[0]["State"] = "running"
        result = reconcile.CommandResult(json.dumps(rows).encode(), b"")
        with mock.patch.object(reconcile, "_run_command", return_value=result):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile._compose_ps(
                    "anhuan-f111-repair-0123456789ab4def8fedcba987654321", 60
                )

    def test_same_tag_with_different_actual_image_changes_runtime_digest(self) -> None:
        services = {name: "a" * 64 for name in reconcile.EXPECTED_SERVICES}
        bases = {
            "dockerfile:api:python:3.11-slim": "b" * 64,
            "dockerfile:web:node:22-alpine": "c" * 64,
            "dockerfile:web:nginx:1.27-alpine": "d" * 64,
        }
        first = reconcile._runtime_document(
            static_inventory_sha256="e" * 64,
            service_images=services,
            base_images=bases,
            build_provenance=self._provenance(),
        )
        services["api"] = "f" * 64
        second = reconcile._runtime_document(
            static_inventory_sha256="e" * 64,
            service_images=services,
            base_images=bases,
            build_provenance=self._provenance(),
        )
        self.assertNotEqual(
            first["runtime_inventory_sha256"],
            second["runtime_inventory_sha256"],
        )

    def test_runtime_evidence_is_owner_only_and_cannot_be_overwritten(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="anhuan-runtime-evidence-", dir="/private/tmp"))
        root.chmod(0o700)
        try:
            document = reconcile._runtime_document(
                static_inventory_sha256="a" * 64,
                service_images={name: "b" * 64 for name in reconcile.EXPECTED_SERVICES},
                base_images={"dockerfile:api:python:3.11-slim": "c" * 64},
                build_provenance=self._provenance(),
            )
            reconcile._write_runtime_evidence(root, document)
            target = root / reconcile.RUNTIME_EVIDENCE_NAME
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(json.loads(target.read_bytes()), document)
            with self.assertRaises(FileExistsError):
                reconcile._write_runtime_evidence(root, document)
        finally:
            shutil.rmtree(root)

    def _docker_transcript(self, project: str) -> tuple[dict, list, dict, dict]:
        local = reconcile._local_images(project)
        services: dict[str, dict[str, str]] = {}
        rows: list[dict[str, object]] = []
        containers: dict[str, dict[str, object]] = {}
        image_docs: dict[str, dict[str, object]] = {}
        reference_ids: dict[str, str] = {}
        for index, name in enumerate(sorted(reconcile.EXPECTED_SERVICES), start=1):
            reference = local.get(
                name,
                f"registry.invalid/{name}@sha256:{format(index + 100, '064x')}",
            )
            image_id = reference_ids.setdefault(
                reference, "sha256:" + format(index + 200, "064x")
            )
            container_id = format(index + 300, "064x")
            services[name] = {"image": reference}
            rows.append(
                {
                    "Service": name,
                    "ID": container_id,
                    "State": "exited" if name == "keycloak-provisioner" else "running",
                    "Health": "" if name == "keycloak-provisioner" else "healthy",
                    "ExitCode": 0,
                }
            )
            containers[container_id] = {
                "Id": container_id,
                "Image": image_id,
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": name,
                    }
                },
            }
            if reference in local.values():
                provenance = self._provenance()
                image_docs[reference] = {
                    "Id": image_id,
                    "RepoTags": [reference],
                    "RepoDigests": [],
                    "Config": {
                        "Labels": {
                            **{
                                label: provenance[key]
                                for key, label in reconcile.clean_rebuild.BUILD_PROVENANCE_LABELS.items()
                            },
                            "org.opencontainers.image.revision": provenance[
                                "source_snapshot_sha256"
                            ],
                        }
                    },
                }
            else:
                image_docs[reference] = {
                    "Id": image_id,
                    "RepoTags": [],
                    "RepoDigests": [reference],
                }
        for index, (_bom_ref, reference) in enumerate(
            reconcile._dockerfile_references(), start=1
        ):
            image_docs[reference] = {
                "Id": "sha256:" + format(index + 500, "064x"),
                "RepoTags": [],
                "RepoDigests": [reference],
            }
        return {"name": project, "services": services}, rows, containers, image_docs

    def test_container_inspection_rejects_identity_and_label_tampering(self) -> None:
        project = "anhuan-f111-repair-0123456789ab4def8fedcba987654321"
        _model, rows, containers, _images = self._docker_transcript(project)
        by_service = {str(row["Service"]): row for row in rows}

        def run(_arguments: object, _timeout: int) -> reconcile.CommandResult:
            return reconcile.CommandResult(
                json.dumps(list(containers.values())).encode(), b""
            )

        with mock.patch.object(reconcile, "_run_command", side_effect=run):
            result = reconcile._inspect_containers(project, by_service, 60)
        self.assertEqual(set(result), set(reconcile.EXPECTED_SERVICES))

        first = next(iter(containers.values()))
        first["Config"]["Labels"]["com.docker.compose.project"] = "other"  # type: ignore[index]
        with mock.patch.object(reconcile, "_run_command", side_effect=run):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile._inspect_containers(project, by_service, 60)

    def test_image_and_base_reconciliation_fail_closed_on_one_field_tamper(self) -> None:
        project = "anhuan-f111-repair-0123456789ab4def8fedcba987654321"
        model, rows, containers, image_docs = self._docker_transcript(project)
        service_refs = reconcile._service_images(model, project)
        container_images = {
            name: str(containers[str(row["ID"])]["Image"])
            for name, row in {str(item["Service"]): item for item in rows}.items()
        }
        with mock.patch.object(
            reconcile, "_inspect_image", side_effect=lambda ref, _timeout: image_docs[ref]
        ):
            reconciled = reconcile._reconcile_images(
                service_refs, container_images, project, 60, self._provenance()
            )
            bases = reconcile._reconcile_bases(60)
        self.assertEqual(set(reconciled), set(reconcile.EXPECTED_SERVICES))
        self.assertEqual(len(bases), 3)

        api_ref = service_refs["api"]
        original_api = image_docs[api_ref]
        image_docs[api_ref] = {**original_api, "RepoTags": []}
        with mock.patch.object(
            reconcile, "_inspect_image", side_effect=lambda ref, _timeout: image_docs[ref]
        ):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile._reconcile_images(
                    service_refs, container_images, project, 60, self._provenance()
                )
        image_docs[api_ref] = original_api
        tampered = json.loads(json.dumps(original_api))
        label = reconcile.clean_rebuild.BUILD_PROVENANCE_LABELS[
            "source_snapshot_sha256"
        ]
        tampered["Config"]["Labels"][label] = "0" * 64
        image_docs[api_ref] = tampered
        with mock.patch.object(
            reconcile, "_inspect_image", side_effect=lambda ref, _timeout: image_docs[ref]
        ):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile._reconcile_images(
                    service_refs, container_images, project, 60, self._provenance()
                )

    def test_complete_mocked_docker_transcript_binds_runtime_evidence(self) -> None:
        project = "anhuan-f111-repair-0123456789ab4def8fedcba987654321"
        model, rows, containers, image_docs = self._docker_transcript(project)
        home = Path(
            tempfile.mkdtemp(prefix=project + "-formal-home-", dir="/private/tmp")
        )
        home.chmod(0o700)
        temporary = home / "tmp"
        temporary.mkdir(mode=0o700)

        def run(arguments: list[str], _timeout: int) -> reconcile.CommandResult:
            if arguments[:1] == ["inspect"]:
                values = [containers[value] for value in arguments[1:]]
                return reconcile.CommandResult(json.dumps(values).encode(), b"")
            if arguments[:2] == ["image", "inspect"]:
                return reconcile.CommandResult(
                    json.dumps([image_docs[arguments[2]]]).encode(), b""
                )
            if "config" in arguments:
                return reconcile.CommandResult(json.dumps(model).encode(), b"")
            if "ps" in arguments:
                return reconcile.CommandResult(json.dumps(rows).encode(), b"")
            raise AssertionError(arguments)

        try:
            with (
                mock.patch.object(reconcile, "_verify_docker_trust_base", return_value=None),
                mock.patch.object(reconcile, "_run_command", side_effect=run),
                mock.patch.object(
                    reconcile,
                    "_expected_build_provenance",
                    return_value=self._provenance(),
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(home),
                        "TMPDIR": str(temporary),
                        "F111_REVERSE_PROJECT": project,
                        "F111_FORMAL_RUN_ID": project,
                        "F111_REVERSE_TIMEOUT_SECONDS": "60",
                    },
                    clear=False,
                ),
            ):
                digest = reconcile.reconcile()
            document = json.loads(
                (temporary / reconcile.RUNTIME_EVIDENCE_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(digest, document["runtime_inventory_sha256"])
            self.assertEqual(
                {item["service"] for item in document["services"]},
                set(reconcile.EXPECTED_SERVICES),
            )
            self.assertEqual(document["build_inputs"], self._provenance())
        finally:
            shutil.rmtree(home)


if __name__ == "__main__":
    unittest.main()
