"""P6 synthetic-only automated quality suites and deterministic results."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0009"
down_revision: str | None = "f1_0008"
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
        CREATE TABLE f1.quality_suite (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
          category text NOT NULL CHECK (category IN (
            'ingestion','retrieval','qa','authorization','injection'
          )),
          status text NOT NULL DEFAULT 'active' CHECK (
            status IN ('active','archived')
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT quality_suite_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT quality_suite_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.quality_scenario (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          suite_id uuid NOT NULL,
          scenario_key text NOT NULL CHECK (
            scenario_key ~ '^[a-z0-9][a-z0-9_.-]{0,79}$'
          ),
          scenario_type text NOT NULL CHECK (scenario_type IN (
            'exact_match','threshold','refusal_required','isolation_required',
            'injection_blocked','disagreement_max'
          )),
          severity text NOT NULL CHECK (
            severity IN ('low','medium','high','critical')
          ),
          oracle_config jsonb NOT NULL CHECK (
            jsonb_typeof(oracle_config) = 'object'
            AND octet_length(oracle_config::text) BETWEEN 2 AND 16384
          ),
          synthetic_observation jsonb NOT NULL CHECK (
            jsonb_typeof(synthetic_observation) = 'object'
            AND octet_length(synthetic_observation::text) BETWEEN 2 AND 16384
          ),
          scenario_sha256 text NOT NULL CHECK (
            scenario_sha256 ~ '^[0-9a-f]{64}$'
          ),
          enabled boolean NOT NULL DEFAULT true,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT quality_scenario_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT quality_scenario_suite_key_uq
            UNIQUE (enterprise_id, suite_id, scenario_key),
          CONSTRAINT quality_scenario_suite_enterprise_fk
            FOREIGN KEY (enterprise_id, suite_id)
            REFERENCES f1.quality_suite(enterprise_id, id),
          CONSTRAINT quality_scenario_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.quality_run (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          suite_id uuid NOT NULL,
          status text NOT NULL DEFAULT 'queued' CHECK (
            status IN ('queued','running','passed','failed','cancelled')
          ),
          trigger_kind text NOT NULL DEFAULT 'manual' CHECK (trigger_kind = 'manual'),
          total_count integer NOT NULL DEFAULT 0 CHECK (total_count >= 0),
          passed_count integer NOT NULL DEFAULT 0 CHECK (passed_count >= 0),
          failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
          error_count integer NOT NULL DEFAULT 0 CHECK (error_count >= 0),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          started_at timestamptz,
          completed_at timestamptz,
          CONSTRAINT quality_run_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT quality_run_suite_enterprise_fk
            FOREIGN KEY (enterprise_id, suite_id)
            REFERENCES f1.quality_suite(enterprise_id, id),
          CONSTRAINT quality_run_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT quality_run_counts_ck CHECK (
            passed_count + failed_count + error_count <= total_count
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.quality_result (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          run_id uuid NOT NULL,
          scenario_id uuid NOT NULL,
          status text NOT NULL CHECK (status IN ('passed','failed','error')),
          reason_code text NOT NULL CHECK (
            reason_code ~ '^[A-Z0-9_]{1,80}$'
          ),
          observed_metrics jsonb NOT NULL CHECK (
            jsonb_typeof(observed_metrics) = 'object'
            AND octet_length(observed_metrics::text) BETWEEN 2 AND 16384
          ),
          evidence_sha256 text NOT NULL CHECK (
            evidence_sha256 ~ '^[0-9a-f]{64}$'
          ),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT quality_result_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT quality_result_run_scenario_uq
            UNIQUE (enterprise_id, run_id, scenario_id),
          CONSTRAINT quality_result_run_enterprise_fk
            FOREIGN KEY (enterprise_id, run_id)
            REFERENCES f1.quality_run(enterprise_id, id),
          CONSTRAINT quality_result_scenario_enterprise_fk
            FOREIGN KEY (enterprise_id, scenario_id)
            REFERENCES f1.quality_scenario(enterprise_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.quality_disagreement (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          result_id uuid NOT NULL,
          kind text NOT NULL CHECK (kind IN (
            'parser','ocr','citation','refusal','authorization','injection'
          )),
          left_digest text NOT NULL CHECK (left_digest ~ '^[0-9a-f]{64}$'),
          right_digest text NOT NULL CHECK (right_digest ~ '^[0-9a-f]{64}$'),
          score numeric(8,6) NOT NULL CHECK (score BETWEEN 0 AND 1),
          review_status text NOT NULL DEFAULT 'open' CHECK (
            review_status IN ('open','acknowledged','waived')
          ),
          review_note text CHECK (
            review_note IS NULL OR char_length(review_note) <= 2000
          ),
          reviewed_by_user_id uuid,
          reviewed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT quality_disagreement_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT quality_disagreement_result_uq UNIQUE (enterprise_id, result_id),
          CONSTRAINT quality_disagreement_result_enterprise_fk
            FOREIGN KEY (enterprise_id, result_id)
            REFERENCES f1.quality_result(enterprise_id, id),
          CONSTRAINT quality_disagreement_reviewer_enterprise_fk
            FOREIGN KEY (enterprise_id, reviewed_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT quality_disagreement_review_ck CHECK (
            (review_status = 'open'
             AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL)
            OR (review_status IN ('acknowledged','waived')
                AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX quality_suite_status_idx "
        "ON f1.quality_suite(enterprise_id, status, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX quality_scenario_suite_idx "
        "ON f1.quality_scenario(enterprise_id, suite_id, enabled, scenario_key)"
    )
    op.execute(
        "CREATE INDEX quality_run_suite_idx "
        "ON f1.quality_run(enterprise_id, suite_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX quality_disagreement_review_idx "
        "ON f1.quality_disagreement(enterprise_id, review_status, created_at DESC)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_suite_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P6_SUITE_IDENTITY_IMMUTABLE'; END IF;
          IF OLD.status = 'archived' AND NEW IS DISTINCT FROM OLD
          THEN RAISE EXCEPTION 'P6_SUITE_ARCHIVED'; END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_scenario_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.suite_id <> OLD.suite_id OR NEW.scenario_key <> OLD.scenario_key
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P6_SCENARIO_IDENTITY_IMMUTABLE'; END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_run_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          actual_total bigint;
          actual_passed bigint;
          actual_failed bigint;
          actual_error bigint;
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.suite_id <> OLD.suite_id OR NEW.trigger_kind <> OLD.trigger_kind
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P6_RUN_IDENTITY_IMMUTABLE'; END IF;
          IF NOT (
            (OLD.status = 'queued' AND NEW.status = 'running'
             AND NEW.started_at IS NOT NULL AND NEW.completed_at IS NULL)
            OR (OLD.status = 'queued' AND NEW.status = 'cancelled'
                AND NEW.completed_at IS NOT NULL)
            OR (OLD.status = 'running' AND NEW.status IN ('passed','failed','cancelled')
                AND NEW.started_at = OLD.started_at
                AND NEW.completed_at IS NOT NULL
                AND NEW.total_count = NEW.passed_count + NEW.failed_count + NEW.error_count
                AND (NEW.status <> 'passed'
                     OR (NEW.failed_count = 0 AND NEW.error_count = 0)))
          ) THEN RAISE EXCEPTION 'P6_RUN_TRANSITION_INVALID'; END IF;
          IF NEW.status IN ('passed','failed') THEN
            SELECT count(*),
                   count(*) FILTER (WHERE status = 'passed'),
                   count(*) FILTER (WHERE status = 'failed'),
                   count(*) FILTER (WHERE status = 'error')
              INTO actual_total, actual_passed, actual_failed, actual_error
            FROM f1.quality_result
            WHERE enterprise_id = NEW.enterprise_id AND run_id = NEW.id;
            IF NEW.total_count <> actual_total
               OR NEW.passed_count <> actual_passed
               OR NEW.failed_count <> actual_failed
               OR NEW.error_count <> actual_error
            THEN RAISE EXCEPTION 'P6_RUN_RESULT_COUNTS_MISMATCH'; END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_result_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          run_suite uuid;
          run_status text;
          scenario_suite uuid;
        BEGIN
          SELECT suite_id, status INTO run_suite, run_status
          FROM f1.quality_run
          WHERE enterprise_id = NEW.enterprise_id AND id = NEW.run_id
          FOR UPDATE;
          SELECT suite_id INTO scenario_suite
          FROM f1.quality_scenario
          WHERE enterprise_id = NEW.enterprise_id AND id = NEW.scenario_id;
          IF run_suite IS NULL OR scenario_suite IS NULL
             OR run_suite <> scenario_suite OR run_status <> 'running'
          THEN RAISE EXCEPTION 'P6_RESULT_RUN_SCOPE_INVALID'; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_disagreement_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          result_status text;
          declared_kind text;
        BEGIN
          SELECT result.status, scenario.oracle_config ->> 'disagreement_kind'
            INTO result_status, declared_kind
          FROM f1.quality_result AS result
          JOIN f1.quality_scenario AS scenario
            ON scenario.enterprise_id = result.enterprise_id
           AND scenario.id = result.scenario_id
          WHERE result.enterprise_id = NEW.enterprise_id
            AND result.id = NEW.result_id;
          IF result_status <> 'failed' OR declared_kind IS DISTINCT FROM NEW.kind
          THEN RAISE EXCEPTION 'P6_DISAGREEMENT_RESULT_INVALID'; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p6_guard_quality_disagreement_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid;
        BEGIN
          SELECT actor.user_id INTO actor_id
          FROM f1.enterprise_user AS actor
          JOIN f1.user_profile AS profile ON profile.id = actor.user_id
          WHERE actor.enterprise_id = OLD.enterprise_id
            AND profile.keycloak_sub = f1.current_sub();
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.result_id <> OLD.result_id OR NEW.kind <> OLD.kind
             OR NEW.left_digest <> OLD.left_digest OR NEW.right_digest <> OLD.right_digest
             OR NEW.score <> OLD.score OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'P6_DISAGREEMENT_IDENTITY_IMMUTABLE'; END IF;
          IF OLD.review_status <> 'open'
             OR NEW.review_status NOT IN ('acknowledged','waived')
             OR NEW.reviewed_by_user_id <> actor_id
             OR NEW.reviewed_at IS NULL
          THEN RAISE EXCEPTION 'P6_DISAGREEMENT_TRANSITION_INVALID'; END IF;
          NEW.reviewed_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    triggers = (
        ("p6_quality_suite_update_guard", "quality_suite", "p6_guard_quality_suite_update"),
        ("p6_quality_scenario_update_guard", "quality_scenario", "p6_guard_quality_scenario_update"),
        ("p6_quality_run_update_guard", "quality_run", "p6_guard_quality_run_update"),
        ("p6_quality_disagreement_update_guard", "quality_disagreement", "p6_guard_quality_disagreement_update"),
    )
    for trigger, table, function in triggers:
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON f1.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION f1.{function}()"
        )
    op.execute(
        "CREATE TRIGGER p6_quality_result_insert_guard "
        "BEFORE INSERT ON f1.quality_result FOR EACH ROW "
        "EXECUTE FUNCTION f1.p6_guard_quality_result_insert()"
    )
    op.execute(
        "CREATE TRIGGER p6_quality_disagreement_insert_guard "
        "BEFORE INSERT ON f1.quality_disagreement FOR EACH ROW "
        "EXECUTE FUNCTION f1.p6_guard_quality_disagreement_insert()"
    )
    for signature in (
        "f1.p6_guard_quality_suite_update()",
        "f1.p6_guard_quality_scenario_update()",
        "f1.p6_guard_quality_run_update()",
        "f1.p6_guard_quality_result_insert()",
        "f1.p6_guard_quality_disagreement_insert()",
        "f1.p6_guard_quality_disagreement_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _row_level_security() -> None:
    for table in (
        "quality_suite",
        "quality_scenario",
        "quality_run",
        "quality_result",
        "quality_disagreement",
    ):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    member = """
      enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized(enterprise_id)
    """

    def role_predicate(table: str, roles: str) -> str:
        return f"""
          EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = {table}.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ({roles})
          )
        """

    for table in (
        "quality_suite",
        "quality_scenario",
        "quality_run",
        "quality_result",
        "quality_disagreement",
    ):
        op.execute(
            f"CREATE POLICY p6_{table}_select ON f1.{table} "
            f"FOR SELECT TO f1_api USING ({member})"
        )
    for table in ("quality_suite", "quality_scenario", "quality_run"):
        manager = role_predicate(table, "'super_admin','enterprise_admin'")
        op.execute(
            f"CREATE POLICY p6_{table}_insert ON f1.{table} "
            f"FOR INSERT TO f1_api WITH CHECK ({member} AND {manager})"
        )
        op.execute(
            f"CREATE POLICY p6_{table}_update ON f1.{table} "
            f"FOR UPDATE TO f1_api USING ({member} AND {manager}) "
            f"WITH CHECK ({member} AND {manager})"
        )
    result_manager = role_predicate(
        "quality_result", "'super_admin','enterprise_admin'"
    )
    disagreement_manager = role_predicate(
        "quality_disagreement", "'super_admin','enterprise_admin'"
    )
    disagreement_reviewer = role_predicate(
        "quality_disagreement", "'super_admin','auditor'"
    )
    op.execute(
        f"CREATE POLICY p6_quality_result_insert ON f1.quality_result "
        f"FOR INSERT TO f1_api WITH CHECK ({member} AND {result_manager})"
    )
    op.execute(
        f"CREATE POLICY p6_quality_disagreement_insert ON f1.quality_disagreement "
        f"FOR INSERT TO f1_api WITH CHECK ({member} AND {disagreement_manager})"
    )
    op.execute(
        f"CREATE POLICY p6_quality_disagreement_update ON f1.quality_disagreement "
        f"FOR UPDATE TO f1_api USING ("
        f"{member} AND ({disagreement_manager} OR {disagreement_reviewer})) "
        f"WITH CHECK ({member} AND ({disagreement_manager} OR {disagreement_reviewer}))"
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON f1.quality_suite, f1.quality_scenario, "
        "f1.quality_run, f1.quality_disagreement TO f1_api"
    )
    op.execute("GRANT SELECT, INSERT ON f1.quality_result TO f1_api")
    op.execute("REVOKE UPDATE, DELETE ON f1.quality_result FROM f1_api")
    op.execute(
        "REVOKE DELETE ON f1.quality_suite, f1.quality_scenario, "
        "f1.quality_run, f1.quality_disagreement FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.quality_suite, f1.quality_scenario, f1.quality_run, "
        "f1.quality_result, f1.quality_disagreement FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    for table in (
        "quality_suite",
        "quality_scenario",
        "quality_run",
        "quality_result",
        "quality_disagreement",
    ):
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $p6_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.quality_suite LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.quality_scenario LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.quality_run LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.quality_result LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.quality_disagreement LIMIT 1)
          THEN RAISE EXCEPTION 'P6_DOWNGRADE_REQUIRES_EMPTY_SCOPE'; END IF;
        END
        $p6_downgrade$
        """
    )
    op.execute("DROP TABLE f1.quality_disagreement")
    op.execute("DROP TABLE f1.quality_result")
    op.execute("DROP TABLE f1.quality_run")
    op.execute("DROP TABLE f1.quality_scenario")
    op.execute("DROP TABLE f1.quality_suite")
    op.execute("DROP FUNCTION f1.p6_guard_quality_disagreement_update()")
    op.execute("DROP FUNCTION f1.p6_guard_quality_run_update()")
    op.execute("DROP FUNCTION f1.p6_guard_quality_result_insert()")
    op.execute("DROP FUNCTION f1.p6_guard_quality_disagreement_insert()")
    op.execute("DROP FUNCTION f1.p6_guard_quality_scenario_update()")
    op.execute("DROP FUNCTION f1.p6_guard_quality_suite_update()")
