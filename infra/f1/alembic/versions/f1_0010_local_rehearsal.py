"""P7 local manual production-rehearsal plans and immutable check results."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0010"
down_revision: str | None = "f1_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _tables()
    _guards()
    _row_level_security()
    _grants()


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.rehearsal_plan (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
          status text NOT NULL DEFAULT 'active' CHECK (
            status IN ('draft','active','archived')
          ),
          execution_mode text NOT NULL DEFAULT 'local_manual'
            CHECK (execution_mode = 'local_manual'),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT rehearsal_plan_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT rehearsal_plan_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.rehearsal_check (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          plan_id uuid NOT NULL,
          check_key text NOT NULL CHECK (
            check_key ~ '^[a-z0-9][a-z0-9_.-]{0,79}$'
          ),
          category text NOT NULL CHECK (category IN (
            'service','dependency','backup','restore','security','rollback'
          )),
          label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 200),
          sequence_no integer NOT NULL CHECK (sequence_no BETWEEN 1 AND 10000),
          required boolean NOT NULL DEFAULT true,
          enabled boolean NOT NULL DEFAULT true,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT rehearsal_check_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT rehearsal_check_plan_key_uq
            UNIQUE (enterprise_id, plan_id, check_key),
          CONSTRAINT rehearsal_check_plan_sequence_uq
            UNIQUE (enterprise_id, plan_id, sequence_no),
          CONSTRAINT rehearsal_check_plan_enterprise_fk
            FOREIGN KEY (enterprise_id, plan_id)
            REFERENCES f1.rehearsal_plan(enterprise_id, id),
          CONSTRAINT rehearsal_check_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.rehearsal_run (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          plan_id uuid NOT NULL,
          status text NOT NULL DEFAULT 'planned' CHECK (
            status IN ('planned','running','passed','failed','cancelled')
          ),
          total_count integer NOT NULL DEFAULT 0 CHECK (total_count >= 0),
          passed_count integer NOT NULL DEFAULT 0 CHECK (passed_count >= 0),
          failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
          blocked_count integer NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
          pending_count integer NOT NULL DEFAULT 0 CHECK (pending_count >= 0),
          rollback_required boolean NOT NULL DEFAULT false,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          started_at timestamptz,
          completed_at timestamptz,
          CONSTRAINT rehearsal_run_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT rehearsal_run_plan_enterprise_fk
            FOREIGN KEY (enterprise_id, plan_id)
            REFERENCES f1.rehearsal_plan(enterprise_id, id),
          CONSTRAINT rehearsal_run_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT rehearsal_run_counts_ck CHECK (
            passed_count + failed_count + blocked_count + pending_count = total_count
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.rehearsal_check_result (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          run_id uuid NOT NULL,
          check_id uuid NOT NULL,
          check_key text NOT NULL CHECK (
            check_key ~ '^[a-z0-9][a-z0-9_.-]{0,79}$'
          ),
          category text NOT NULL CHECK (category IN (
            'service','dependency','backup','restore','security','rollback'
          )),
          label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 200),
          sequence_no integer NOT NULL CHECK (sequence_no BETWEEN 1 AND 10000),
          required boolean NOT NULL,
          status text NOT NULL DEFAULT 'pending' CHECK (
            status IN ('pending','passed','failed','blocked')
          ),
          reason_code text,
          evidence_sha256 text,
          recorded_by_user_id uuid,
          recorded_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT rehearsal_check_result_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT rehearsal_check_result_run_check_uq
            UNIQUE (enterprise_id, run_id, check_id),
          CONSTRAINT rehearsal_check_result_run_enterprise_fk
            FOREIGN KEY (enterprise_id, run_id)
            REFERENCES f1.rehearsal_run(enterprise_id, id),
          CONSTRAINT rehearsal_check_result_check_enterprise_fk
            FOREIGN KEY (enterprise_id, check_id)
            REFERENCES f1.rehearsal_check(enterprise_id, id),
          CONSTRAINT rehearsal_check_result_recorder_enterprise_fk
            FOREIGN KEY (enterprise_id, recorded_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT rehearsal_check_result_terminal_ck CHECK (
            (status = 'pending' AND reason_code IS NULL
             AND evidence_sha256 IS NULL AND recorded_by_user_id IS NULL
             AND recorded_at IS NULL)
            OR (status IN ('passed','failed','blocked')
                AND reason_code ~ '^[A-Z0-9_]{1,80}$'
                AND evidence_sha256 ~ '^[0-9a-f]{64}$'
                AND recorded_by_user_id IS NOT NULL AND recorded_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX rehearsal_plan_status_idx "
        "ON f1.rehearsal_plan(enterprise_id, status, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX rehearsal_check_plan_idx "
        "ON f1.rehearsal_check(enterprise_id, plan_id, enabled, sequence_no)"
    )
    op.execute(
        "CREATE INDEX rehearsal_run_plan_idx "
        "ON f1.rehearsal_run(enterprise_id, plan_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX rehearsal_result_run_idx "
        "ON f1.rehearsal_check_result(enterprise_id, run_id, sequence_no)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p7_guard_rehearsal_plan_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.execution_mode <> OLD.execution_mode
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P7_PLAN_IDENTITY_IMMUTABLE'; END IF;
          IF OLD.status = 'archived' AND NEW IS DISTINCT FROM OLD
          THEN RAISE EXCEPTION 'P7_PLAN_ARCHIVED'; END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p7_guard_rehearsal_check_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE plan_status text;
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.plan_id <> OLD.plan_id OR NEW.check_key <> OLD.check_key
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P7_CHECK_IDENTITY_IMMUTABLE'; END IF;
          SELECT status INTO plan_status FROM f1.rehearsal_plan
          WHERE enterprise_id = OLD.enterprise_id AND id = OLD.plan_id;
          IF plan_status = 'archived'
          THEN RAISE EXCEPTION 'P7_PLAN_ARCHIVED'; END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p7_guard_rehearsal_result_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          run_plan uuid;
          run_status text;
          source_check f1.rehearsal_check%ROWTYPE;
        BEGIN
          SELECT plan_id, status INTO run_plan, run_status
          FROM f1.rehearsal_run
          WHERE enterprise_id = NEW.enterprise_id AND id = NEW.run_id
          FOR UPDATE;
          SELECT * INTO source_check FROM f1.rehearsal_check
          WHERE enterprise_id = NEW.enterprise_id AND id = NEW.check_id;
          IF run_status <> 'planned' OR source_check.id IS NULL
             OR source_check.plan_id <> run_plan OR source_check.enabled IS NOT TRUE
             OR NEW.check_key <> source_check.check_key
             OR NEW.category <> source_check.category
             OR NEW.label <> source_check.label
             OR NEW.sequence_no <> source_check.sequence_no
             OR NEW.required <> source_check.required
             OR NEW.status <> 'pending'
          THEN RAISE EXCEPTION 'P7_RESULT_SNAPSHOT_INVALID'; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p7_guard_rehearsal_result_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          run_status text;
          actor_id uuid;
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.run_id <> OLD.run_id OR NEW.check_id <> OLD.check_id
             OR NEW.check_key <> OLD.check_key OR NEW.category <> OLD.category
             OR NEW.label <> OLD.label OR NEW.sequence_no <> OLD.sequence_no
             OR NEW.required <> OLD.required OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P7_RESULT_SNAPSHOT_IMMUTABLE'; END IF;
          SELECT status INTO run_status FROM f1.rehearsal_run
          WHERE enterprise_id = OLD.enterprise_id AND id = OLD.run_id
          FOR UPDATE;
          SELECT actor.user_id INTO actor_id
          FROM f1.enterprise_user AS actor
          JOIN f1.user_profile AS profile ON profile.id = actor.user_id
          WHERE actor.enterprise_id = OLD.enterprise_id
            AND profile.keycloak_sub = f1.current_sub();
          IF run_status <> 'running' OR OLD.status <> 'pending'
             OR NEW.status NOT IN ('passed','failed','blocked')
             OR actor_id IS NULL
          THEN RAISE EXCEPTION 'P7_RESULT_TRANSITION_INVALID'; END IF;
          NEW.recorded_by_user_id := actor_id;
          NEW.recorded_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p7_guard_rehearsal_run_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          actual_total bigint;
          actual_passed bigint;
          actual_failed bigint;
          actual_blocked bigint;
          actual_pending bigint;
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.plan_id <> OLD.plan_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P7_RUN_IDENTITY_IMMUTABLE'; END IF;
          SELECT count(*),
                 count(*) FILTER (WHERE status = 'passed'),
                 count(*) FILTER (WHERE status = 'failed'),
                 count(*) FILTER (WHERE status = 'blocked'),
                 count(*) FILTER (WHERE status = 'pending')
            INTO actual_total, actual_passed, actual_failed,
                 actual_blocked, actual_pending
          FROM f1.rehearsal_check_result
          WHERE enterprise_id = NEW.enterprise_id AND run_id = NEW.id;
          NEW.total_count := actual_total;
          NEW.passed_count := actual_passed;
          NEW.failed_count := actual_failed;
          NEW.blocked_count := actual_blocked;
          NEW.pending_count := actual_pending;
          IF OLD.status = 'planned' AND NEW.status = 'running' THEN
            IF actual_total = 0 OR actual_pending <> actual_total
               OR NEW.started_at IS NULL OR NEW.completed_at IS NOT NULL
            THEN RAISE EXCEPTION 'P7_RUN_START_INVALID'; END IF;
            NEW.rollback_required := false;
          ELSIF OLD.status = 'running' AND NEW.status IN ('passed','failed') THEN
            IF actual_pending <> 0 OR NEW.completed_at IS NULL
            THEN RAISE EXCEPTION 'P7_RUN_INCOMPLETE'; END IF;
            IF NEW.status = 'passed'
               AND (actual_failed <> 0 OR actual_blocked <> 0
                    OR actual_passed <> actual_total)
            THEN RAISE EXCEPTION 'P7_RUN_PASS_GATE_FAILED'; END IF;
            IF NEW.status = 'failed' AND actual_failed + actual_blocked = 0
            THEN RAISE EXCEPTION 'P7_RUN_FAILURE_GATE_INVALID'; END IF;
            NEW.rollback_required := (NEW.status = 'failed');
          ELSIF OLD.status = 'running' AND NEW.status = 'cancelled' THEN
            IF NEW.completed_at IS NULL
            THEN RAISE EXCEPTION 'P7_RUN_CANCEL_INVALID'; END IF;
            NEW.rollback_required := (actual_failed + actual_blocked > 0);
          ELSE
            RAISE EXCEPTION 'P7_RUN_TRANSITION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    update_triggers = (
        ("p7_rehearsal_plan_update_guard", "rehearsal_plan", "p7_guard_rehearsal_plan_update"),
        ("p7_rehearsal_check_update_guard", "rehearsal_check", "p7_guard_rehearsal_check_update"),
        ("p7_rehearsal_run_update_guard", "rehearsal_run", "p7_guard_rehearsal_run_update"),
        ("p7_rehearsal_result_update_guard", "rehearsal_check_result", "p7_guard_rehearsal_result_update"),
    )
    for trigger, table, function in update_triggers:
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON f1.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION f1.{function}()"
        )
    op.execute(
        "CREATE TRIGGER p7_rehearsal_result_insert_guard "
        "BEFORE INSERT ON f1.rehearsal_check_result FOR EACH ROW "
        "EXECUTE FUNCTION f1.p7_guard_rehearsal_result_insert()"
    )
    for signature in (
        "f1.p7_guard_rehearsal_plan_update()",
        "f1.p7_guard_rehearsal_check_update()",
        "f1.p7_guard_rehearsal_run_update()",
        "f1.p7_guard_rehearsal_result_insert()",
        "f1.p7_guard_rehearsal_result_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _row_level_security() -> None:
    tables = (
        "rehearsal_plan",
        "rehearsal_check",
        "rehearsal_run",
        "rehearsal_check_result",
    )
    for table in tables:
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY p7_{table}_select ON f1.{table} FOR SELECT TO f1_api "
            "USING (enterprise_id = f1.current_enterprise_id() "
            "AND f1.session_authorized(enterprise_id))"
        )

    def actor_roles(table: str, roles: str) -> str:
        return f"""
          EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = {table}.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ({roles})
          )
        """

    for table in ("rehearsal_plan", "rehearsal_check", "rehearsal_run"):
        manager = actor_roles(table, "'super_admin','enterprise_admin'")
        op.execute(
            f"CREATE POLICY p7_{table}_insert ON f1.{table} FOR INSERT TO f1_api "
            f"WITH CHECK (enterprise_id = f1.current_enterprise_id() AND {manager})"
        )
        op.execute(
            f"CREATE POLICY p7_{table}_update ON f1.{table} FOR UPDATE TO f1_api "
            f"USING (enterprise_id = f1.current_enterprise_id() AND {manager}) "
            f"WITH CHECK (enterprise_id = f1.current_enterprise_id() AND {manager})"
        )
    run_operator = actor_roles("rehearsal_run", "'auditor'")
    op.execute(
        "CREATE POLICY p7_rehearsal_run_operator_lock ON f1.rehearsal_run "
        "FOR UPDATE TO f1_api USING ("
        "enterprise_id = f1.current_enterprise_id() AND status = 'running' AND "
        + run_operator
        + ") WITH CHECK (false)"
    )
    result_manager = actor_roles(
        "rehearsal_check_result", "'super_admin','enterprise_admin'"
    )
    result_operator = actor_roles(
        "rehearsal_check_result", "'super_admin','enterprise_admin','auditor'"
    )
    op.execute(
        "CREATE POLICY p7_rehearsal_result_insert ON f1.rehearsal_check_result "
        "FOR INSERT TO f1_api WITH CHECK ("
        "enterprise_id = f1.current_enterprise_id() AND " + result_manager + ")"
    )
    op.execute(
        "CREATE POLICY p7_rehearsal_result_update ON f1.rehearsal_check_result "
        "FOR UPDATE TO f1_api USING ("
        "enterprise_id = f1.current_enterprise_id() AND " + result_operator + ") "
        "WITH CHECK (enterprise_id = f1.current_enterprise_id() AND "
        + result_operator + ")"
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON f1.rehearsal_plan, f1.rehearsal_check, "
        "f1.rehearsal_run, f1.rehearsal_check_result TO f1_api"
    )
    op.execute(
        "REVOKE DELETE ON f1.rehearsal_plan, f1.rehearsal_check, "
        "f1.rehearsal_run, f1.rehearsal_check_result FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.rehearsal_plan, f1.rehearsal_check, f1.rehearsal_run, "
        "f1.rehearsal_check_result FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    for table in (
        "rehearsal_plan",
        "rehearsal_check",
        "rehearsal_run",
        "rehearsal_check_result",
    ):
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $p7_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.rehearsal_plan LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.rehearsal_check LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.rehearsal_run LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.rehearsal_check_result LIMIT 1)
          THEN RAISE EXCEPTION 'P7_DOWNGRADE_REQUIRES_EMPTY_SCOPE'; END IF;
        END
        $p7_downgrade$
        """
    )
    op.execute("DROP TABLE f1.rehearsal_check_result")
    op.execute("DROP TABLE f1.rehearsal_run")
    op.execute("DROP TABLE f1.rehearsal_check")
    op.execute("DROP TABLE f1.rehearsal_plan")
    op.execute("DROP FUNCTION f1.p7_guard_rehearsal_result_update()")
    op.execute("DROP FUNCTION f1.p7_guard_rehearsal_result_insert()")
    op.execute("DROP FUNCTION f1.p7_guard_rehearsal_run_update()")
    op.execute("DROP FUNCTION f1.p7_guard_rehearsal_check_update()")
    op.execute("DROP FUNCTION f1.p7_guard_rehearsal_plan_update()")
