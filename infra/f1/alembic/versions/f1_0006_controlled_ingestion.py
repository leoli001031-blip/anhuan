"""P3 controlled ingestion: logical versions, quarantine and safe previews."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1_0006"
down_revision: str | None = "f1_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_upload_task()
    _tables()
    _state_guards()
    _row_level_security()
    _grants()


def _extend_upload_task() -> None:
    op.execute(
        "ALTER TABLE f1.upload_task "
        "ADD COLUMN pipeline_kind text NOT NULL DEFAULT 'fixture_index', "
        "ADD COLUMN processing_stage text NOT NULL DEFAULT 'received', "
        "ADD COLUMN quarantine_status text NOT NULL DEFAULT 'not_applicable', "
        "ADD COLUMN scan_verdict text NOT NULL DEFAULT 'not_required', "
        "ADD COLUMN scanner_engine text, ADD COLUMN scanner_version text, "
        "ADD COLUMN signature_version text, ADD COLUMN preview_kind text, "
        "ADD COLUMN preview_status text NOT NULL DEFAULT 'not_required', "
        "ADD COLUMN preview_sha256 text, "
        "ADD COLUMN preview_unit_count int NOT NULL DEFAULT 0, "
        "ADD COLUMN resource_policy_version text NOT NULL DEFAULT 'fixture-v1', "
        "ADD COLUMN released_at timestamptz, ADD COLUMN rejected_at timestamptz"
    )
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT upload_task_sha_idem_uq")
    op.execute(
        "CREATE UNIQUE INDEX upload_task_fixture_sha_uq ON f1.upload_task "
        "(enterprise_id, content_sha256) WHERE pipeline_kind='fixture_index'"
    )
    # Existing fixture-index rows may legitimately have more than one task for
    # a document.  Scope the new one-task-per-document invariant to P3 only.
    op.execute(
        "CREATE UNIQUE INDEX upload_task_p3_document_uq ON f1.upload_task "
        "(enterprise_id, document_id) WHERE pipeline_kind='controlled_ingestion'"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT "
        "upload_task_enterprise_id_id_document_uq "
        "UNIQUE (enterprise_id, id, document_id)"
    )
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT upload_task_object_state_ck")
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_object_state_ck "
        "CHECK (object_state IN ('reserved','quarantined','ready','write_failed'))"
    )
    checks = {
        "upload_task_pipeline_kind_ck":
            "pipeline_kind IN ('fixture_index','controlled_ingestion')",
        "upload_task_processing_stage_ck":
            "processing_stage IN ('received','scanning','validating','previewing',"
            "'ready','retry_wait','rejected','failed')",
        "upload_task_quarantine_status_ck":
            "quarantine_status IN ('not_applicable','held','released','blocked')",
        "upload_task_scan_verdict_ck":
            "scan_verdict IN ('not_required','queued','scanning','clean','infected',"
            "'error','unavailable')",
        "upload_task_preview_status_ck":
            "preview_status IN ('not_required','blocked','queued','generating',"
            "'ready','failed')",
        "upload_task_preview_kind_ck":
            "preview_kind IS NULL OR preview_kind IN ('page_text','sheet_grid','image')",
        "upload_task_preview_sha_ck":
            "preview_sha256 IS NULL OR preview_sha256 ~ '^[0-9a-f]{64}$'",
        "upload_task_preview_count_ck":
            "preview_unit_count >= 0 AND preview_unit_count <= 128",
        "upload_task_p3_state_ck":
            "pipeline_kind <> 'controlled_ingestion' OR "
            "((object_state='reserved' AND quarantine_status='held' "
            "AND released_at IS NULL AND rejected_at IS NULL) OR "
            "(object_state='write_failed' AND quarantine_status IN "
            "('not_applicable','blocked') AND released_at IS NULL) OR "
            "(object_state='quarantined' AND quarantine_status IN ('held','blocked') "
            "AND released_at IS NULL) OR "
            "(object_state='ready' AND status='done' AND processing_stage='ready' "
            "AND scan_verdict='clean' AND preview_status='ready' "
            "AND quarantine_status IN ('held','released')))",
        "upload_task_p3_release_ck":
            "pipeline_kind <> 'controlled_ingestion' OR "
            "((released_at IS NULL AND quarantine_status <> 'released') OR "
            "(released_at IS NOT NULL AND object_state='ready' "
            "AND quarantine_status='released' "
            "AND processing_stage='ready' "
            "AND scan_verdict='clean' AND preview_status='ready'))",
        "upload_task_p3_reject_ck":
            "pipeline_kind <> 'controlled_ingestion' OR rejected_at IS NULL OR "
            "(object_state='quarantined' AND quarantine_status='blocked' "
            "AND processing_stage='rejected' AND status='failed' "
            "AND released_at IS NULL)",
    }
    for name, expression in checks.items():
        op.execute(
            f"ALTER TABLE f1.upload_task ADD CONSTRAINT {name} CHECK ({expression})"
        )
    op.execute(
        "CREATE INDEX upload_task_p3_stage_idx ON f1.upload_task "
        "(enterprise_id, pipeline_kind, processing_stage, next_attempt_at)"
    )


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.document_record (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          plant_id uuid,
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
          latest_version_no int NOT NULL DEFAULT 0 CHECK (latest_version_no >= 0),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT document_record_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT document_record_plant_enterprise_fk
            FOREIGN KEY (enterprise_id, plant_id)
            REFERENCES f1.plant(enterprise_id, id),
          CONSTRAINT document_record_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.document_version (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_record_id uuid NOT NULL,
          version_no int NOT NULL CHECK (version_no > 0),
          source_document_id uuid NOT NULL,
          upload_task_id uuid NOT NULL,
          display_filename text NOT NULL CHECK (char_length(display_filename) BETWEEN 1 AND 255),
          idempotency_key_sha256 text NOT NULL
            CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{64}$'),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT document_version_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT document_version_record_version_uq
            UNIQUE (enterprise_id, document_record_id, version_no),
          CONSTRAINT document_version_idempotency_uq
            UNIQUE (enterprise_id, idempotency_key_sha256),
          CONSTRAINT document_version_task_uq
            UNIQUE (enterprise_id, upload_task_id),
          CONSTRAINT document_version_record_enterprise_fk
            FOREIGN KEY (enterprise_id, document_record_id)
            REFERENCES f1.document_record(enterprise_id, id),
          CONSTRAINT document_version_source_enterprise_fk
            FOREIGN KEY (enterprise_id, source_document_id)
            REFERENCES f1.document(enterprise_id, id),
          CONSTRAINT document_version_task_enterprise_fk
            FOREIGN KEY (enterprise_id, upload_task_id)
            REFERENCES f1.upload_task(enterprise_id, id),
          CONSTRAINT document_version_task_source_fk
            FOREIGN KEY (enterprise_id, upload_task_id, source_document_id)
            REFERENCES f1.upload_task(enterprise_id, id, document_id),
          CONSTRAINT document_version_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.document_preview_unit (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_version_id uuid NOT NULL,
          unit_kind text NOT NULL CHECK (unit_kind IN ('page_text','worksheet_grid','image')),
          ordinal int NOT NULL CHECK (ordinal > 0 AND ordinal <= 128),
          label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 128),
          width_px int CHECK (width_px IS NULL OR width_px BETWEEN 1 AND 10000),
          height_px int CHECK (height_px IS NULL OR height_px BETWEEN 1 AND 10000),
          row_count int CHECK (row_count IS NULL OR row_count BETWEEN 0 AND 100000),
          column_count int CHECK (column_count IS NULL OR column_count BETWEEN 0 AND 256),
          content_type text NOT NULL CHECK (content_type IN
            ('image/jpeg','application/json')),
          object_key text NOT NULL CHECK (char_length(object_key) BETWEEN 32 AND 160),
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          size_bytes bigint NOT NULL CHECK (
            (unit_kind IN ('page_text','worksheet_grid')
             AND content_type='application/json'
             AND size_bytes BETWEEN 1 AND 262144)
            OR (unit_kind='image' AND content_type='image/jpeg'
                AND size_bytes BETWEEN 1 AND 20971520
                AND width_px BETWEEN 1 AND 10000
                AND height_px BETWEEN 1 AND 10000)
          ),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT document_preview_unit_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT document_preview_unit_version_ordinal_uq
            UNIQUE (enterprise_id, document_version_id, ordinal),
          CONSTRAINT document_preview_unit_version_enterprise_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX document_record_enterprise_updated_idx "
        "ON f1.document_record(enterprise_id, updated_at DESC, id)"
    )
    op.execute(
        "CREATE INDEX document_version_record_created_idx "
        "ON f1.document_version(enterprise_id, document_record_id, version_no DESC)"
    )


def _state_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p3_guard_document_record_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'P3_DOCUMENT_RECORD_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.latest_version_no < OLD.latest_version_no THEN
            RAISE EXCEPTION 'P3_DOCUMENT_VERSION_ROLLBACK';
          END IF;
          IF OLD.status = 'archived' AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'P3_DOCUMENT_ARCHIVE_FINAL';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER p3_document_record_update_guard BEFORE UPDATE "
        "ON f1.document_record FOR EACH ROW "
        "EXECUTE FUNCTION f1.p3_guard_document_record_update()"
    )


def _manager_predicate(alias: str) -> str:
    return f"""{alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND EXISTS (
        SELECT 1 FROM f1.enterprise_user AS member
        JOIN f1.user_profile AS profile ON profile.id = member.user_id
        WHERE member.enterprise_id = {alias}.enterprise_id
          AND profile.keycloak_sub = f1.current_sub()
          AND member.role IN ('super_admin','enterprise_admin','plant_admin')
      )"""


def _worker_version_predicate(alias: str) -> str:
    return f"""{alias}.enterprise_id = f1.current_enterprise_id()
      AND EXISTS (
        SELECT 1 FROM f1.upload_task AS p3_task
        WHERE p3_task.enterprise_id = {alias}.enterprise_id
          AND p3_task.id = {alias}.upload_task_id
          AND p3_task.pipeline_kind = 'controlled_ingestion'
          AND p3_task.id = f1.current_task_id()
          AND p3_task.lease_token = f1.current_lease_token()
          AND p3_task.lease_until > statement_timestamp()
      )"""


def _row_level_security() -> None:
    for table in ("document_record", "document_version", "document_preview_unit"):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY p3_document_record_select ON f1.document_record "
        f"FOR SELECT TO f1_api USING ({_manager_predicate('document_record')})"
    )
    op.execute(
        f"CREATE POLICY p3_document_record_insert ON f1.document_record "
        f"FOR INSERT TO f1_api WITH CHECK ({_manager_predicate('document_record')} "
        "AND EXISTS (SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_record.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()))"
    )
    op.execute(
        f"CREATE POLICY p3_document_record_update ON f1.document_record "
        f"FOR UPDATE TO f1_api USING ({_manager_predicate('document_record')}) "
        f"WITH CHECK ({_manager_predicate('document_record')})"
    )
    op.execute(
        f"CREATE POLICY p3_document_version_select ON f1.document_version "
        f"FOR SELECT TO f1_api USING ({_manager_predicate('document_version')})"
    )
    op.execute(
        f"CREATE POLICY p3_document_version_insert ON f1.document_version "
        f"FOR INSERT TO f1_api WITH CHECK ({_manager_predicate('document_version')} "
        "AND EXISTS (SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_version.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()))"
    )
    op.execute(
        f"CREATE POLICY p3_document_version_worker_select ON f1.document_version "
        f"FOR SELECT TO f1_worker USING ({_worker_version_predicate('document_version')})"
    )
    op.execute(
        f"CREATE POLICY p3_preview_unit_select ON f1.document_preview_unit "
        f"FOR SELECT TO f1_api USING ({_manager_predicate('document_preview_unit')})"
    )
    worker_preview = """enterprise_id = f1.current_enterprise_id() AND EXISTS (
      SELECT 1 FROM f1.document_version AS version
      JOIN f1.upload_task AS p3_task
        ON p3_task.enterprise_id=version.enterprise_id
       AND p3_task.id=version.upload_task_id
      WHERE version.enterprise_id=document_preview_unit.enterprise_id
        AND version.id=document_preview_unit.document_version_id
        AND p3_task.pipeline_kind='controlled_ingestion'
        AND p3_task.id=f1.current_task_id()
        AND p3_task.lease_token=f1.current_lease_token()
        AND p3_task.lease_until > statement_timestamp()
    )"""
    op.execute(
        "CREATE POLICY p3_preview_unit_worker_select ON f1.document_preview_unit "
        f"FOR SELECT TO f1_worker USING ({worker_preview})"
    )
    op.execute(
        "CREATE POLICY p3_preview_unit_worker_insert ON f1.document_preview_unit "
        f"FOR INSERT TO f1_worker WITH CHECK ({worker_preview})"
    )


def _grants() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.document_record TO f1_api")
    op.execute("GRANT SELECT, INSERT ON f1.document_version TO f1_api")
    op.execute("GRANT SELECT ON f1.document_preview_unit TO f1_api")
    op.execute("GRANT SELECT ON f1.document_version TO f1_worker")
    op.execute("GRANT SELECT, INSERT ON f1.document_preview_unit TO f1_worker")
    op.execute(
        "REVOKE ALL ON f1.document_record, f1.document_version, "
        "f1.document_preview_unit FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON f1.document_record FROM f1_worker")
    op.execute("REVOKE UPDATE, DELETE ON f1.document_version FROM f1_api, f1_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON f1.document_preview_unit FROM f1_api")
    op.execute("REVOKE UPDATE, DELETE ON f1.document_preview_unit FROM f1_worker")


def downgrade() -> None:
    # Refuse before any destructive DDL. Restoring the old global SHA unique
    # contract cannot preserve controlled-ingestion versions losslessly.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM f1.upload_task "
        "WHERE pipeline_kind='controlled_ingestion') THEN "
        "RAISE EXCEPTION 'P3_DOWNGRADE_DATA_PRESENT'; END IF; END $$"
    )
    op.execute("DROP TABLE IF EXISTS f1.document_preview_unit CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.document_version CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.document_record CASCADE")
    op.execute("DROP FUNCTION IF EXISTS f1.p3_guard_document_record_update()")
    op.execute("DROP INDEX IF EXISTS f1.upload_task_p3_stage_idx")
    op.execute("DROP INDEX IF EXISTS f1.upload_task_p3_document_uq")
    op.execute(
        "ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS "
        "upload_task_enterprise_id_id_document_uq"
    )
    op.execute("DROP INDEX IF EXISTS f1.upload_task_fixture_sha_uq")
    for constraint in (
        "upload_task_p3_reject_ck", "upload_task_p3_release_ck",
        "upload_task_p3_state_ck", "upload_task_preview_count_ck",
        "upload_task_preview_sha_ck", "upload_task_preview_kind_ck",
        "upload_task_preview_status_ck", "upload_task_scan_verdict_ck",
        "upload_task_processing_stage_ck", "upload_task_quarantine_status_ck",
        "upload_task_pipeline_kind_ck",
        "upload_task_object_state_ck",
    ):
        op.execute(f"ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_object_state_ck "
        "CHECK (object_state IN ('reserved','ready','write_failed'))"
    )
    op.execute(
        "ALTER TABLE f1.upload_task DROP COLUMN IF EXISTS rejected_at, "
        "DROP COLUMN IF EXISTS released_at, DROP COLUMN IF EXISTS resource_policy_version, "
        "DROP COLUMN IF EXISTS preview_unit_count, DROP COLUMN IF EXISTS preview_sha256, "
        "DROP COLUMN IF EXISTS preview_status, DROP COLUMN IF EXISTS preview_kind, "
        "DROP COLUMN IF EXISTS signature_version, DROP COLUMN IF EXISTS scanner_version, "
        "DROP COLUMN IF EXISTS scanner_engine, DROP COLUMN IF EXISTS scan_verdict, "
        "DROP COLUMN IF EXISTS quarantine_status, "
        "DROP COLUMN IF EXISTS processing_stage, DROP COLUMN IF EXISTS pipeline_kind"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_sha_idem_uq "
        "UNIQUE (enterprise_id, content_sha256)"
    )
