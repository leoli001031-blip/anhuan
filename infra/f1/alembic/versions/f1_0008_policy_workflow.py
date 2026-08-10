"""P5 internal policy source, review, publication, and impact workflow."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0008"
down_revision: str | None = "f1_0007"
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
        CREATE TABLE f1.policy_source (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
          publisher text NOT NULL CHECK (char_length(publisher) BETWEEN 1 AND 200),
          source_type text NOT NULL CHECK (source_type IN (
            'law','regulation','standard','guidance','internal'
          )),
          jurisdiction text NOT NULL CHECK (
            char_length(jurisdiction) BETWEEN 1 AND 120
          ),
          source_reference text NOT NULL CHECK (
            char_length(source_reference) BETWEEN 1 AND 500
          ),
          status text NOT NULL DEFAULT 'active' CHECK (
            status IN ('active','archived')
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT policy_source_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT policy_source_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.policy_version (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          source_id uuid NOT NULL,
          version_number integer NOT NULL CHECK (version_number > 0),
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
          domain text NOT NULL CHECK (domain IN (
            'safety','health','environment','fire','chemical','general'
          )),
          effect_status text NOT NULL DEFAULT 'unknown' CHECK (
            effect_status IN ('unknown','not_effective','effective','expired')
          ),
          issued_on date,
          effective_from date,
          effective_to date,
          summary text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 4000),
          document_version_id uuid,
          document_sha256 text,
          workflow_status text NOT NULL DEFAULT 'draft' CHECK (
            workflow_status IN (
              'draft','in_review','approved','rejected','published','superseded'
            )
          ),
          submitted_by_user_id uuid,
          submitted_at timestamptz,
          approved_by_user_id uuid,
          approved_at timestamptz,
          published_by_user_id uuid,
          published_at timestamptz,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT policy_version_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT policy_version_source_number_uq
            UNIQUE (enterprise_id, source_id, version_number),
          CONSTRAINT policy_version_source_enterprise_fk
            FOREIGN KEY (enterprise_id, source_id)
            REFERENCES f1.policy_source(enterprise_id, id),
          CONSTRAINT policy_version_document_enterprise_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id),
          CONSTRAINT policy_version_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT policy_version_submitter_enterprise_fk
            FOREIGN KEY (enterprise_id, submitted_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT policy_version_approver_enterprise_fk
            FOREIGN KEY (enterprise_id, approved_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT policy_version_publisher_enterprise_fk
            FOREIGN KEY (enterprise_id, published_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT policy_version_document_pair_ck CHECK (
            (document_version_id IS NULL AND document_sha256 IS NULL)
            OR (document_version_id IS NOT NULL
                AND document_sha256 ~ '^[0-9a-f]{64}$')
          ),
          CONSTRAINT policy_version_effective_window_ck CHECK (
            effective_from IS NULL OR effective_to IS NULL
            OR effective_to >= effective_from
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX policy_version_published_uq
        ON f1.policy_version(enterprise_id, source_id)
        WHERE workflow_status = 'published'
        """
    )
    op.execute(
        """
        CREATE TABLE f1.policy_review_event (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          policy_version_id uuid NOT NULL,
          action text NOT NULL CHECK (
            action IN ('submitted','approved','rejected','published')
          ),
          comment text CHECK (comment IS NULL OR char_length(comment) <= 2000),
          actor_user_id uuid NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT policy_review_event_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT policy_review_event_version_enterprise_fk
            FOREIGN KEY (enterprise_id, policy_version_id)
            REFERENCES f1.policy_version(enterprise_id, id),
          CONSTRAINT policy_review_event_actor_enterprise_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.policy_impact_candidate (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          policy_version_id uuid NOT NULL,
          domain text NOT NULL CHECK (domain IN (
            'safety','health','environment','fire','chemical','general'
          )),
          scope_note text NOT NULL CHECK (
            char_length(scope_note) BETWEEN 1 AND 4000
          ),
          priority text NOT NULL CHECK (
            priority IN ('low','medium','high','critical')
          ),
          status text NOT NULL DEFAULT 'open' CHECK (
            status IN ('open','accepted','dismissed')
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT policy_impact_candidate_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT policy_impact_candidate_version_enterprise_fk
            FOREIGN KEY (enterprise_id, policy_version_id)
            REFERENCES f1.policy_version(enterprise_id, id),
          CONSTRAINT policy_impact_candidate_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.policy_impact_task (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          impact_candidate_id uuid NOT NULL,
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
          owner_user_id uuid NOT NULL,
          due_at timestamptz,
          status text NOT NULL DEFAULT 'open' CHECK (
            status IN ('open','in_progress','completed','dismissed')
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT policy_impact_task_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT policy_impact_task_candidate_enterprise_fk
            FOREIGN KEY (enterprise_id, impact_candidate_id)
            REFERENCES f1.policy_impact_candidate(enterprise_id, id),
          CONSTRAINT policy_impact_task_owner_enterprise_fk
            FOREIGN KEY (enterprise_id, owner_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT policy_impact_task_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX policy_source_search_idx "
        "ON f1.policy_source(enterprise_id, status, source_type)"
    )
    op.execute(
        "CREATE INDEX policy_version_search_idx "
        "ON f1.policy_version(enterprise_id, domain, effect_status, workflow_status)"
    )
    op.execute(
        "CREATE INDEX policy_review_event_version_idx "
        "ON f1.policy_review_event(enterprise_id, policy_version_id, occurred_at)"
    )
    op.execute(
        "CREATE INDEX policy_impact_candidate_status_idx "
        "ON f1.policy_impact_candidate(enterprise_id, status, priority)"
    )
    op.execute(
        "CREATE INDEX policy_impact_task_owner_idx "
        "ON f1.policy_impact_task(enterprise_id, owner_user_id, status, due_at)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_source_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P5_POLICY_SOURCE_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.status = 'archived' AND NEW.status <> 'archived' THEN
            RAISE EXCEPTION 'P5_POLICY_SOURCE_ARCHIVED';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_version_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          actor_id uuid;
          actor_role text;
        BEGIN
          SELECT actor.user_id, actor.role
          INTO actor_id, actor_role
          FROM f1.enterprise_user AS actor
          JOIN f1.user_profile AS profile ON profile.id = actor.user_id
          WHERE actor.enterprise_id = OLD.enterprise_id
            AND profile.keycloak_sub = f1.current_sub();
          IF actor_id IS NULL THEN
            RAISE EXCEPTION 'P5_POLICY_ACTOR_REQUIRED';
          END IF;
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.source_id <> OLD.source_id
             OR NEW.version_number <> OLD.version_number
             OR NEW.title <> OLD.title
             OR NEW.domain <> OLD.domain
             OR NEW.effect_status <> OLD.effect_status
             OR NEW.issued_on IS DISTINCT FROM OLD.issued_on
             OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
             OR NEW.effective_to IS DISTINCT FROM OLD.effective_to
             OR NEW.summary <> OLD.summary
             OR NEW.document_version_id IS DISTINCT FROM OLD.document_version_id
             OR NEW.document_sha256 IS DISTINCT FROM OLD.document_sha256
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P5_POLICY_VERSION_CONTENT_IMMUTABLE';
          END IF;
          IF NOT (
            (OLD.workflow_status IN ('draft','rejected')
             AND NEW.workflow_status = 'in_review'
             AND actor_role IN ('super_admin','enterprise_admin')
             AND NEW.submitted_by_user_id = actor_id
             AND NEW.submitted_at IS NOT NULL
             AND NEW.approved_by_user_id IS NULL
             AND NEW.approved_at IS NULL
             AND NEW.published_by_user_id IS NULL
             AND NEW.published_at IS NULL)
            OR (OLD.workflow_status = 'in_review'
                AND NEW.workflow_status = 'approved'
                AND actor_role IN ('super_admin','auditor')
                AND NEW.submitted_by_user_id = OLD.submitted_by_user_id
                AND NEW.submitted_at = OLD.submitted_at
                AND NEW.approved_by_user_id = actor_id
                AND NEW.approved_at IS NOT NULL
                AND NEW.approved_by_user_id <> OLD.submitted_by_user_id
                AND NEW.published_by_user_id IS NULL
                AND NEW.published_at IS NULL)
            OR (OLD.workflow_status = 'in_review'
                AND NEW.workflow_status = 'rejected'
                AND actor_role IN ('super_admin','auditor')
                AND actor_id <> OLD.submitted_by_user_id
                AND NEW.submitted_by_user_id = OLD.submitted_by_user_id
                AND NEW.submitted_at = OLD.submitted_at
                AND NEW.approved_by_user_id IS NULL
                AND NEW.approved_at IS NULL
                AND NEW.published_by_user_id IS NULL
                AND NEW.published_at IS NULL)
            OR (OLD.workflow_status = 'approved'
                AND NEW.workflow_status = 'published'
                AND actor_role IN ('super_admin','enterprise_admin')
                AND NEW.submitted_by_user_id = OLD.submitted_by_user_id
                AND NEW.submitted_at = OLD.submitted_at
                AND NEW.approved_by_user_id = OLD.approved_by_user_id
                AND NEW.approved_at = OLD.approved_at
                AND NEW.published_by_user_id = actor_id
                AND NEW.published_at IS NOT NULL)
            OR (OLD.workflow_status = 'published'
                AND NEW.workflow_status = 'superseded'
                AND actor_role IN ('super_admin','enterprise_admin')
                AND NEW.submitted_by_user_id = OLD.submitted_by_user_id
                AND NEW.submitted_at = OLD.submitted_at
                AND NEW.approved_by_user_id = OLD.approved_by_user_id
                AND NEW.approved_at = OLD.approved_at
                AND NEW.published_by_user_id = OLD.published_by_user_id
                AND NEW.published_at = OLD.published_at)
          ) THEN
            RAISE EXCEPTION 'P5_POLICY_VERSION_TRANSITION_INVALID';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_require_policy_review_event()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          expected_action text;
          actor_id uuid;
        BEGIN
          expected_action := CASE NEW.workflow_status
            WHEN 'in_review' THEN 'submitted'
            WHEN 'approved' THEN 'approved'
            WHEN 'rejected' THEN 'rejected'
            WHEN 'published' THEN 'published'
            ELSE NULL
          END;
          IF expected_action IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT actor.user_id INTO actor_id
          FROM f1.enterprise_user AS actor
          JOIN f1.user_profile AS profile ON profile.id = actor.user_id
          WHERE actor.enterprise_id = NEW.enterprise_id
            AND profile.keycloak_sub = f1.current_sub();
          IF actor_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM f1.policy_review_event AS event
            WHERE event.enterprise_id = NEW.enterprise_id
              AND event.policy_version_id = NEW.id
              AND event.action = expected_action
              AND event.actor_user_id = actor_id
              AND event.occurred_at >= NEW.updated_at
          ) THEN
            RAISE EXCEPTION 'P5_POLICY_REVIEW_EVENT_REQUIRED';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_review_event_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          version_row f1.policy_version%ROWTYPE;
        BEGIN
          NEW.occurred_at := statement_timestamp();
          SELECT * INTO version_row
          FROM f1.policy_version
          WHERE enterprise_id = NEW.enterprise_id
            AND id = NEW.policy_version_id;
          IF version_row.id IS NULL
             OR NEW.occurred_at < version_row.updated_at
             OR NOT (
               (NEW.action = 'submitted'
                AND version_row.workflow_status = 'in_review'
                AND NEW.actor_user_id = version_row.submitted_by_user_id)
               OR (NEW.action = 'approved'
                   AND version_row.workflow_status = 'approved'
                   AND NEW.actor_user_id = version_row.approved_by_user_id)
               OR (NEW.action = 'rejected'
                   AND version_row.workflow_status = 'rejected'
                   AND NEW.actor_user_id <> version_row.submitted_by_user_id)
               OR (NEW.action = 'published'
                   AND version_row.workflow_status = 'published'
                   AND NEW.actor_user_id = version_row.published_by_user_id)
             )
          THEN
            RAISE EXCEPTION 'P5_POLICY_REVIEW_EVENT_MISMATCH';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_impact_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM f1.policy_version AS version
            WHERE version.enterprise_id = NEW.enterprise_id
              AND version.id = NEW.policy_version_id
              AND version.workflow_status IN ('approved','published')
          ) THEN
            RAISE EXCEPTION 'P5_POLICY_IMPACT_VERSION_NOT_READY';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_impact_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.policy_version_id <> OLD.policy_version_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P5_POLICY_IMPACT_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.status IN ('accepted','dismissed')
             AND NEW IS DISTINCT FROM OLD
          THEN
            RAISE EXCEPTION 'P5_POLICY_IMPACT_TERMINAL';
          END IF;
          IF NEW.status <> OLD.status AND NOT (
            OLD.status = 'open' AND NEW.status IN ('accepted','dismissed')
          ) THEN
            RAISE EXCEPTION 'P5_POLICY_IMPACT_TRANSITION_INVALID';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_impact_task_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.impact_candidate_id <> OLD.impact_candidate_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P5_POLICY_TASK_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.status IN ('completed','dismissed')
             AND NEW IS DISTINCT FROM OLD
          THEN
            RAISE EXCEPTION 'P5_POLICY_TASK_TERMINAL';
          END IF;
          IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'open'
             AND NEW.status IN ('in_progress','completed','dismissed'))
            OR (OLD.status = 'in_progress'
                AND NEW.status IN ('completed','dismissed'))
          ) THEN
            RAISE EXCEPTION 'P5_POLICY_TASK_TRANSITION_INVALID';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    triggers = (
        ("p5_policy_source_update_guard", "policy_source", "p5_guard_policy_source_update", "UPDATE"),
        ("p5_policy_version_update_guard", "policy_version", "p5_guard_policy_version_update", "UPDATE"),
        ("p5_policy_review_event_insert_guard", "policy_review_event", "p5_guard_policy_review_event_insert", "INSERT"),
        ("p5_policy_impact_insert_guard", "policy_impact_candidate", "p5_guard_policy_impact_insert", "INSERT"),
        ("p5_policy_impact_update_guard", "policy_impact_candidate", "p5_guard_policy_impact_update", "UPDATE"),
        ("p5_policy_impact_task_update_guard", "policy_impact_task", "p5_guard_policy_impact_task_update", "UPDATE"),
    )
    for trigger, table, function, action in triggers:
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE {action} ON f1.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION f1.{function}()"
        )
    op.execute(
        "CREATE CONSTRAINT TRIGGER p5_policy_review_event_required "
        "AFTER UPDATE ON f1.policy_version DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION f1.p5_require_policy_review_event()"
    )
    for signature in (
        "f1.p5_guard_policy_source_update()",
        "f1.p5_guard_policy_version_update()",
        "f1.p5_require_policy_review_event()",
        "f1.p5_guard_policy_review_event_insert()",
        "f1.p5_guard_policy_impact_insert()",
        "f1.p5_guard_policy_impact_update()",
        "f1.p5_guard_policy_impact_task_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _row_level_security() -> None:
    for table in (
        "policy_source",
        "policy_version",
        "policy_review_event",
        "policy_impact_candidate",
        "policy_impact_task",
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
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = {table}.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ({roles})
          )
        """

    source_manager = role_predicate(
        "policy_source", "'super_admin','enterprise_admin'"
    )
    version_manager = role_predicate(
        "policy_version", "'super_admin','enterprise_admin'"
    )
    version_reviewer = role_predicate(
        "policy_version", "'super_admin','auditor'"
    )
    event_manager = role_predicate(
        "policy_review_event", "'super_admin','enterprise_admin'"
    )
    event_reviewer = role_predicate(
        "policy_review_event", "'super_admin','auditor'"
    )
    op.execute(
        f"CREATE POLICY p5_policy_source_select ON f1.policy_source "
        f"FOR SELECT TO f1_api USING ({member})"
    )
    op.execute(
        f"CREATE POLICY p5_policy_source_insert ON f1.policy_source "
        f"FOR INSERT TO f1_api WITH CHECK ({member} AND {source_manager})"
    )
    op.execute(
        f"CREATE POLICY p5_policy_source_update ON f1.policy_source "
        f"FOR UPDATE TO f1_api USING ({member} AND {source_manager}) "
        f"WITH CHECK ({member} AND {source_manager})"
    )
    op.execute(
        f"""
        CREATE POLICY p5_policy_version_select ON f1.policy_version
        FOR SELECT TO f1_api USING (
          {member}
          AND (
            workflow_status IN ('published','superseded')
            OR {version_manager}
            OR {version_reviewer}
          )
        )
        """
    )
    op.execute(
        f"CREATE POLICY p5_policy_version_insert ON f1.policy_version "
        f"FOR INSERT TO f1_api WITH CHECK ({member} AND {version_manager})"
    )
    op.execute(
        f"CREATE POLICY p5_policy_version_update ON f1.policy_version "
        f"FOR UPDATE TO f1_api USING ("
        f"{member} AND ({version_manager} OR {version_reviewer})) "
        f"WITH CHECK ({member} AND ({version_manager} OR {version_reviewer}))"
    )
    op.execute(
        f"""
        CREATE POLICY p5_policy_review_event_select ON f1.policy_review_event
        FOR SELECT TO f1_api USING (
          {member}
          AND EXISTS (
            SELECT 1 FROM f1.policy_version AS version
            WHERE version.enterprise_id = policy_review_event.enterprise_id
              AND version.id = policy_review_event.policy_version_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY p5_policy_review_event_insert ON f1.policy_review_event
        FOR INSERT TO f1_api WITH CHECK (
          {member}
          AND ({event_manager} OR {event_reviewer})
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = policy_review_event.actor_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    for table in ("policy_impact_candidate", "policy_impact_task"):
        table_manager = role_predicate(
            table, "'super_admin','enterprise_admin'"
        )
        table_reviewer = role_predicate(table, "'super_admin','auditor'")
        op.execute(
            f"CREATE POLICY p5_{table}_select ON f1.{table} "
            f"FOR SELECT TO f1_api USING ({member})"
        )
        op.execute(
            f"CREATE POLICY p5_{table}_insert ON f1.{table} "
            f"FOR INSERT TO f1_api WITH CHECK ("
            f"{member} AND ({table_manager} OR {table_reviewer}))"
        )
    impact_manager = role_predicate(
        "policy_impact_candidate", "'super_admin','enterprise_admin'"
    )
    impact_reviewer = role_predicate(
        "policy_impact_candidate", "'super_admin','auditor'"
    )
    op.execute(
        f"CREATE POLICY p5_policy_impact_candidate_update "
        f"ON f1.policy_impact_candidate FOR UPDATE TO f1_api "
        f"USING ({member} AND ({impact_manager} OR {impact_reviewer})) "
        f"WITH CHECK ({member} AND ({impact_manager} OR {impact_reviewer}))"
    )
    task_manager = role_predicate(
        "policy_impact_task", "'super_admin','enterprise_admin'"
    )
    task_reviewer = role_predicate(
        "policy_impact_task", "'super_admin','auditor'"
    )
    op.execute(
        f"""
        CREATE POLICY p5_policy_impact_task_update ON f1.policy_impact_task
        FOR UPDATE TO f1_api
        USING (
          {member}
          AND (
            {task_manager}
            OR {task_reviewer}
            OR EXISTS (
              SELECT 1 FROM f1.user_profile AS profile
              WHERE profile.id = policy_impact_task.owner_user_id
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        WITH CHECK (
          {member}
          AND (
            {task_manager}
            OR {task_reviewer}
            OR EXISTS (
              SELECT 1 FROM f1.user_profile AS profile
              WHERE profile.id = policy_impact_task.owner_user_id
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        """
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON f1.policy_source, f1.policy_version, "
        "f1.policy_impact_candidate, f1.policy_impact_task TO f1_api"
    )
    op.execute("GRANT SELECT, INSERT ON f1.policy_review_event TO f1_api")
    op.execute(
        "REVOKE UPDATE, DELETE ON f1.policy_review_event FROM f1_api"
    )
    op.execute(
        "REVOKE DELETE ON f1.policy_source, f1.policy_version, "
        "f1.policy_impact_candidate, f1.policy_impact_task FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.policy_source, f1.policy_version, "
        "f1.policy_review_event, f1.policy_impact_candidate, "
        "f1.policy_impact_task FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    for table in (
        "policy_source",
        "policy_version",
        "policy_review_event",
        "policy_impact_candidate",
        "policy_impact_task",
    ):
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $p5_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.policy_source LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.policy_version LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.policy_review_event LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.policy_impact_candidate LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.policy_impact_task LIMIT 1)
          THEN
            RAISE EXCEPTION 'P5_DOWNGRADE_REQUIRES_EMPTY_SCOPE';
          END IF;
        END
        $p5_downgrade$
        """
    )
    op.execute("DROP TABLE f1.policy_impact_task")
    op.execute("DROP TABLE f1.policy_impact_candidate")
    op.execute("DROP TABLE f1.policy_review_event")
    op.execute("DROP TABLE f1.policy_version")
    op.execute("DROP TABLE f1.policy_source")
    op.execute("DROP FUNCTION f1.p5_guard_policy_impact_task_update()")
    op.execute("DROP FUNCTION f1.p5_guard_policy_impact_update()")
    op.execute("DROP FUNCTION f1.p5_guard_policy_impact_insert()")
    op.execute("DROP FUNCTION f1.p5_guard_policy_review_event_insert()")
    op.execute("DROP FUNCTION f1.p5_guard_policy_version_update()")
    op.execute("DROP FUNCTION f1.p5_require_policy_review_event()")
    op.execute("DROP FUNCTION f1.p5_guard_policy_source_update()")
