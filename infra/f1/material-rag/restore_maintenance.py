"""Offline bootstrap maintenance after a dedicated material-RAG restore.

Must run as ``f0d_bootstrap`` with ``session_replication_role=origin`` in one
transaction.  DELETE jobs, then DELETE units, then clear binding ciphertext
into ``deleted``.  Never UPDATE non-terminal jobs to failed, never replica,
never bypass row security, never definer functions, never disable triggers.
"""
from __future__ import annotations

from typing import Any

import psycopg


class RestoreMaintenanceError(RuntimeError):
    def __init__(self, code: str, mutation_observed: int = 0) -> None:
        self.code = code
        self.mutation_observed = mutation_observed
        super().__init__(code)


MAINTENANCE_SQL = (
    "DELETE FROM f1.material_rag_job; "
    "DELETE FROM f1.material_rag_unit; "
    "UPDATE f1.material_rag_scope_binding SET "
    "dataset_ref_ciphertext=NULL, dataset_ref_sha256=NULL, "
    "dataset_ref_aad_sha256=NULL, status='deleted', error_reason=NULL"
)


def _require_bootstrap_origin(connection: psycopg.Connection) -> tuple[str, str, str]:
    replica = connection.execute("SHOW session_replication_role").fetchone()
    if replica is None or replica[0] != "origin":
        raise RestoreMaintenanceError("REPLICA_ROLE_FORBIDDEN")
    identity = connection.execute(
        "SELECT current_user, session_user, "
        "current_setting('session_replication_role')"
    ).fetchone()
    if (
        identity is None
        or identity[0] != "f0d_bootstrap"
        or identity[1] != "f0d_bootstrap"
        or identity[2] != "origin"
    ):
        raise RestoreMaintenanceError("BOOTSTRAP_IDENTITY_MISMATCH")
    return str(identity[0]), str(identity[1]), str(identity[2])


def residual_counts(connection: psycopg.Connection) -> dict[str, int]:
    _require_bootstrap_origin(connection)
    job = connection.execute("SELECT count(*) FROM f1.material_rag_job").fetchone()
    unit = connection.execute("SELECT count(*) FROM f1.material_rag_unit").fetchone()
    live = connection.execute(
        "SELECT count(*) FROM f1.material_rag_job "
        "WHERE status='running' AND lease_until > statement_timestamp()"
    ).fetchone()
    provisioning = connection.execute(
        "SELECT count(*) FROM f1.material_rag_scope_binding "
        "WHERE status='provisioning'"
    ).fetchone()
    deleted_secrets = connection.execute(
        "SELECT count(*) FROM f1.material_rag_scope_binding "
        "WHERE status='deleted' AND ("
        "dataset_ref_ciphertext IS NOT NULL OR "
        "dataset_ref_sha256 IS NOT NULL OR "
        "dataset_ref_aad_sha256 IS NOT NULL)"
    ).fetchone()
    orphan = connection.execute(
        "SELECT count(*) FROM f1.material_rag_unit AS unit "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM f1.document_version AS version "
        "JOIN f1.document_record AS record "
        "ON record.enterprise_id=version.enterprise_id "
        "AND record.id=version.document_record_id "
        "JOIN f1.upload_task AS task "
        "ON task.enterprise_id=version.enterprise_id "
        "AND task.id=version.upload_task_id "
        "WHERE version.enterprise_id=unit.enterprise_id "
        "AND version.id=unit.document_version_id "
        "AND version.document_record_id=unit.document_record_id "
        "AND record.knowledge_scope_id=unit.knowledge_scope_id "
        "AND record.id=unit.document_record_id "
        "AND task.content_sha256=unit.source_sha256)"
    ).fetchone()
    return {
        "deleted_secret": int(deleted_secrets[0]) if deleted_secrets else 0,
        "job": int(job[0]) if job else 0,
        "live_lease": int(live[0]) if live else 0,
        "orphan": int(orphan[0]) if orphan else 0,
        "provisioning": int(provisioning[0]) if provisioning else 0,
        "unit": int(unit[0]) if unit else 0,
    }


def _stage_snapshot(
    connection: psycopg.Connection,
) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]:
    jobs = connection.execute(
        "SELECT id FROM f1.material_rag_job ORDER BY id"
    ).fetchall()
    units = connection.execute(
        "SELECT id FROM f1.material_rag_unit ORDER BY id"
    ).fetchall()
    bindings = connection.execute(
        "SELECT id, status, dataset_ref_sha256 "
        "FROM f1.material_rag_scope_binding ORDER BY id"
    ).fetchall()
    return (tuple(jobs), tuple(units), tuple(bindings))


def run_restore_maintenance(
    connection: psycopg.Connection, *, inject_failure: bool = False
) -> dict[str, Any]:
    current_user, session_user, role = _require_bootstrap_origin(connection)
    before = residual_counts(connection)
    snapshot = _stage_snapshot(connection)
    try:
        connection.execute("DELETE FROM f1.material_rag_job")
        after_first = residual_counts(connection)
        mutation = 0 if after_first["job"] == before["job"] else 1
        if inject_failure:
            raise RestoreMaintenanceError(
                "MAINTENANCE_INJECTED_FAILURE",
                mutation_observed=mutation,
            )
        connection.execute("DELETE FROM f1.material_rag_unit")
        connection.execute(
            "UPDATE f1.material_rag_scope_binding SET "
            "dataset_ref_ciphertext=NULL, dataset_ref_sha256=NULL, "
            "dataset_ref_aad_sha256=NULL, status='deleted', error_reason=NULL"
        )
        after = residual_counts(connection)
        if any(value != 0 for value in after.values()):
            raise RestoreMaintenanceError("MAINTENANCE_RESIDUAL")
        connection.commit()
        return {
            "after": after,
            "before": before,
            "identity": {
                "current_user": current_user,
                "replication_role": role,
                "session_user": session_user,
            },
        }
    except Exception:
        connection.rollback()
        if _stage_snapshot(connection) != snapshot:
            raise RestoreMaintenanceError("MAINTENANCE_SNAPSHOT_ROLLBACK_FAILED")
        raise
