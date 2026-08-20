"""Fresh-process crash child and journal recovery probe.

Child restores until DB_RESTORED, then pauses for supervisor SIGKILL.
Recovery reads a 0600 receipt plus journal and deletes only exact
three-label new containers and volumes.  Stdout is a closed count JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ProbeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical(payload: dict[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def run_child(*, ready: Path, receipt: Path) -> int:
    os.environ["MATERIAL_RAG_RESTORE_WAIT_AFTER"] = "DB_RESTORED"
    os.environ["MATERIAL_RAG_CRASH_READY"] = str(ready)
    os.environ["MATERIAL_RAG_CRASH_RECEIPT"] = str(receipt)
    os.environ.pop("MATERIAL_RAG_RESTORE_CRASH_AFTER", None)
    from infra.f1.material_rag_backup_restore import BackupRestoreStack

    stack = BackupRestoreStack()
    stack.install_fakes()
    try:
        stack.start()
        stack.seed_backup_world()
        stack.put_source_objects()
        package = stack.create_package()
        stack.restore_package(package)
        raise ProbeError("CRASH_CHILD_DID_NOT_PAUSE")
    finally:
        stack.stop()


def run_recover(*, receipt_path: Path, tamper: bool) -> int:
    from infra.f1.material_rag_backup_restore import (
        BackupRestoreStack,
        DockerIdentityDestroyer,
        RestoreRecoveryError,
        SCOPE,
        _read_crash_receipt,
        recover_from_journal,
        verify_package,
    )

    receipt = _read_crash_receipt(receipt_path)
    stack = BackupRestoreStack.attach_from_receipt(receipt)
    live = stack.capture_core_identities()
    if tamper:
        live = [
            {
                "handle": item.get("handle"),
                "id": item["id"],
                "kind": item["kind"],
                "labels": {
                    "io.anhuan.parent-project-id": item["labels"][
                        "io.anhuan.parent-project-id"
                    ],
                    "io.anhuan.project-id": "c" * 32,
                    "io.anhuan.scope": item["labels"]["io.anhuan.scope"],
                },
            }
            for item in live
        ]
    package = Path(str(receipt["package_path"]))
    manifest = json.loads((package / "manifest.json").read_bytes().decode("ascii"))

    def package_check() -> None:
        verify_package(
            package,
            expected_project_id=str(receipt["project_id"]),
            expected_parent_project_id=str(receipt["parent_project_id"]),
            expected_database=str(manifest["database"]),
            expected_scope=SCOPE,
        )

    destroyer = DockerIdentityDestroyer(stack)
    try:
        result = recover_from_journal(
            Path(str(receipt["journal_path"])),
            expected_scope=SCOPE,
            expected_project_id=str(receipt["project_id"]),
            expected_parent_project_id=str(receipt["parent_project_id"]),
            expected_dump_sha256=str(receipt["package_dump_sha256"]),
            expected_tree_sha256=str(receipt["package_tree_sha256"]),
            live=live,
            destroyer=destroyer,
            package_check=package_check,
        )
    except RestoreRecoveryError as error:
        if tamper and error.code == "RESOURCE_LABEL_MISMATCH":
            raise ProbeError("RESOURCE_LABEL_MISMATCH") from error
        raise ProbeError(error.code) from error
    if tamper:
        raise ProbeError("CRASH_TAMPER_FALSE_GREEN")
    payload = {
        "deleted": int(result["deleted"]),
        "package_reverified": int(result["package_reverified"]),
        "rebuild_started": int(result["rebuild_started"]),
        "remaining_abort_id_count": int(result["remaining_abort_id_count"]),
    }
    sys.stdout.write(_canonical(payload))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="crash_recovery_probe")
    parser.add_argument("role", choices=("child", "recover"))
    parser.add_argument("--ready")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--tamper-labels", action="store_true")
    arguments = parser.parse_args()
    try:
        receipt = Path(arguments.receipt)
        if arguments.role == "child":
            if not arguments.ready:
                raise ProbeError("CRASH_READY_MISSING")
            return run_child(ready=Path(arguments.ready), receipt=receipt)
        return run_recover(receipt_path=receipt, tamper=arguments.tamper_labels)
    except ProbeError as error:
        sys.stderr.write(error.code + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
