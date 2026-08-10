"""P2 Wave 1: tenant-bound service cases and personnel assignments."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0005"
down_revision: str | None = "f1_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _tables()
    _wave2_tables()
    _wave3_tables()
    _wave4_tables()
    _state_guards()
    _wave2_state_guards()
    _wave3_state_guards()
    _wave4_state_guards()
    _row_level_security()
    _wave2_row_level_security()
    _wave3_row_level_security()
    _wave4_row_level_security()
    _grants()


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.service_case (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          plant_id uuid,
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          description text CHECK (
            description IS NULL OR char_length(description) <= 4000
          ),
          service_type text NOT NULL CHECK (
            char_length(service_type) BETWEEN 1 AND 64
          ),
          status text NOT NULL DEFAULT 'planned' CHECK (status IN (
            'planned','in_progress','completed','closed','cancelled'
          )),
          planned_start_at timestamptz,
          planned_end_at timestamptz,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT service_case_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT service_case_plant_enterprise_fk
            FOREIGN KEY (enterprise_id, plant_id)
            REFERENCES f1.plant(enterprise_id, id),
          CONSTRAINT service_case_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT service_case_planned_window_ck CHECK (
            planned_start_at IS NULL OR planned_end_at IS NULL
            OR planned_end_at >= planned_start_at
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.service_assignment (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          service_case_id uuid NOT NULL,
          assignee_user_id uuid NOT NULL,
          assigned_by_user_id uuid NOT NULL,
          capacity text NOT NULL CHECK (capacity IN (
            'employee','consultant','partner'
          )),
          status text NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','accepted','rejected','revoked'
          )),
          assigned_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          responded_at timestamptz,
          revoked_at timestamptz,
          CONSTRAINT service_assignment_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT service_assignment_case_enterprise_fk
            FOREIGN KEY (enterprise_id, service_case_id)
            REFERENCES f1.service_case(enterprise_id, id),
          CONSTRAINT service_assignment_assignee_enterprise_fk
            FOREIGN KEY (enterprise_id, assignee_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT service_assignment_assigner_enterprise_fk
            FOREIGN KEY (enterprise_id, assigned_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT service_assignment_state_time_ck CHECK (
            (status = 'pending' AND responded_at IS NULL AND revoked_at IS NULL)
            OR (status IN ('accepted','rejected')
                AND responded_at IS NOT NULL AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX service_assignment_active_uq
        ON f1.service_assignment(
          enterprise_id, service_case_id, assignee_user_id, capacity
        )
        WHERE status IN ('pending','accepted')
        """
    )
    op.execute(
        "CREATE INDEX service_case_enterprise_status_idx "
        "ON f1.service_case(enterprise_id, status, planned_start_at)"
    )
    op.execute(
        "CREATE INDEX service_assignment_assignee_idx "
        "ON f1.service_assignment(enterprise_id, assignee_user_id, status)"
    )


def _state_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p2_guard_service_case_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P2_SERVICE_CASE_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'planned' AND NEW.status IN ('in_progress','cancelled'))
            OR (OLD.status = 'in_progress'
                AND NEW.status IN ('completed','cancelled'))
            OR (OLD.status = 'completed' AND NEW.status = 'closed')
          ) THEN
            RAISE EXCEPTION 'P2_SERVICE_CASE_TRANSITION_INVALID';
          END IF;
          IF NEW.status <> OLD.status AND (
            NEW.plant_id IS DISTINCT FROM OLD.plant_id
            OR NEW.title IS DISTINCT FROM OLD.title
            OR NEW.description IS DISTINCT FROM OLD.description
            OR NEW.service_type IS DISTINCT FROM OLD.service_type
            OR NEW.planned_start_at IS DISTINCT FROM OLD.planned_start_at
            OR NEW.planned_end_at IS DISTINCT FROM OLD.planned_end_at
          ) THEN
            RAISE EXCEPTION 'P2_SERVICE_CASE_TRANSITION_FIELDS_IMMUTABLE';
          END IF;
          IF NEW.status = OLD.status AND NOT EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = NEW.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          ) THEN
            RAISE EXCEPTION 'P2_SERVICE_CASE_MANAGER_EDIT_REQUIRED';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p2_guard_service_assignment_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.service_case_id <> OLD.service_case_id
             OR NEW.assignee_user_id <> OLD.assignee_user_id
             OR NEW.assigned_by_user_id <> OLD.assigned_by_user_id
             OR NEW.capacity <> OLD.capacity
             OR NEW.assigned_at <> OLD.assigned_at
          THEN
            RAISE EXCEPTION 'P2_SERVICE_ASSIGNMENT_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.status = OLD.status OR NOT (
            (OLD.status = 'pending'
             AND NEW.status IN ('accepted','rejected','revoked'))
            OR (OLD.status = 'accepted' AND NEW.status = 'revoked')
          ) THEN
            RAISE EXCEPTION 'P2_SERVICE_ASSIGNMENT_TRANSITION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER p2_service_case_update_guard
        BEFORE UPDATE ON f1.service_case
        FOR EACH ROW EXECUTE FUNCTION f1.p2_guard_service_case_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER p2_service_assignment_update_guard
        BEFORE UPDATE ON f1.service_assignment
        FOR EACH ROW EXECUTE FUNCTION f1.p2_guard_service_assignment_update()
        """
    )
    for signature in (
        "f1.p2_guard_service_case_update()",
        "f1.p2_guard_service_assignment_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _wave2_tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.site_visit (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          service_case_id uuid NOT NULL,
          status text NOT NULL DEFAULT 'planned' CHECK (status IN (
            'planned','in_progress','completed','cancelled'
          )),
          planned_start_at timestamptz,
          planned_end_at timestamptz,
          started_at timestamptz,
          completed_at timestamptz,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT site_visit_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT site_visit_case_enterprise_fk
            FOREIGN KEY (enterprise_id, service_case_id)
            REFERENCES f1.service_case(enterprise_id, id),
          CONSTRAINT site_visit_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT site_visit_planned_window_ck CHECK (
            planned_start_at IS NULL OR planned_end_at IS NULL
            OR planned_end_at >= planned_start_at
          ),
          CONSTRAINT site_visit_state_time_ck CHECK (
            (status = 'planned'
             AND started_at IS NULL AND completed_at IS NULL)
            OR (status = 'in_progress'
                AND started_at IS NOT NULL AND completed_at IS NULL)
            OR (status = 'completed'
                AND started_at IS NOT NULL AND completed_at IS NOT NULL
                AND completed_at >= started_at)
            OR (status = 'cancelled' AND completed_at IS NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.finding (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          service_case_id uuid,
          site_visit_id uuid,
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 8000),
          severity text NOT NULL CHECK (severity IN (
            'low','medium','high','critical'
          )),
          responsible_user_id uuid,
          due_at timestamptz NOT NULL,
          status text NOT NULL DEFAULT 'open' CHECK (status IN (
            'open','rectifying','submitted','reviewing','passed','rejected','closed'
          )),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT finding_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT finding_case_enterprise_fk
            FOREIGN KEY (enterprise_id, service_case_id)
            REFERENCES f1.service_case(enterprise_id, id),
          CONSTRAINT finding_visit_enterprise_fk
            FOREIGN KEY (enterprise_id, site_visit_id)
            REFERENCES f1.site_visit(enterprise_id, id),
          CONSTRAINT finding_responsible_enterprise_fk
            FOREIGN KEY (enterprise_id, responsible_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT finding_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT finding_context_required_ck CHECK (
            service_case_id IS NOT NULL OR site_visit_id IS NOT NULL
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.corrective_action (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          finding_id uuid NOT NULL,
          revision integer NOT NULL CHECK (revision > 0),
          description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 8000),
          submitted_by_user_id uuid NOT NULL,
          submitted_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT corrective_action_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT corrective_action_finding_revision_uq
            UNIQUE (enterprise_id, finding_id, revision),
          CONSTRAINT corrective_action_finding_enterprise_fk
            FOREIGN KEY (enterprise_id, finding_id)
            REFERENCES f1.finding(enterprise_id, id),
          CONSTRAINT corrective_action_submitter_enterprise_fk
            FOREIGN KEY (enterprise_id, submitted_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.finding_review (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          finding_id uuid NOT NULL,
          decision text NOT NULL CHECK (decision IN ('passed','rejected')),
          comment text NOT NULL CHECK (char_length(comment) <= 4000),
          reviewer_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT finding_review_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT finding_review_finding_enterprise_fk
            FOREIGN KEY (enterprise_id, finding_id)
            REFERENCES f1.finding(enterprise_id, id),
          CONSTRAINT finding_review_reviewer_enterprise_fk
            FOREIGN KEY (enterprise_id, reviewer_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX site_visit_case_status_idx "
        "ON f1.site_visit(enterprise_id, service_case_id, status)"
    )
    op.execute(
        "CREATE INDEX finding_case_status_due_idx "
        "ON f1.finding(enterprise_id, service_case_id, status, due_at)"
    )
    op.execute(
        "CREATE INDEX finding_responsible_status_idx "
        "ON f1.finding(enterprise_id, responsible_user_id, status)"
    )


def _wave3_tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.business_timeline (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          service_case_id uuid NOT NULL,
          event_type text NOT NULL CHECK (
            char_length(event_type) BETWEEN 1 AND 64
          ),
          subject_type text NOT NULL CHECK (
            char_length(subject_type) BETWEEN 1 AND 64
          ),
          subject_id uuid NOT NULL,
          status text CHECK (
            status IS NULL OR char_length(status) BETWEEN 1 AND 64
          ),
          actor_user_id uuid NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT business_timeline_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT business_timeline_case_enterprise_fk
            FOREIGN KEY (enterprise_id, service_case_id)
            REFERENCES f1.service_case(enterprise_id, id),
          CONSTRAINT business_timeline_actor_enterprise_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX business_timeline_case_time_idx "
        "ON f1.business_timeline(enterprise_id, service_case_id, occurred_at, id)"
    )


def _wave4_tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.in_app_notification (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          recipient_user_id uuid NOT NULL,
          timeline_event_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          read_at timestamptz,
          CONSTRAINT in_app_notification_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT in_app_notification_recipient_event_uq
            UNIQUE (enterprise_id, recipient_user_id, timeline_event_id),
          CONSTRAINT in_app_notification_recipient_enterprise_fk
            FOREIGN KEY (enterprise_id, recipient_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT in_app_notification_timeline_enterprise_fk
            FOREIGN KEY (enterprise_id, timeline_event_id)
            REFERENCES f1.business_timeline(enterprise_id, id),
          CONSTRAINT in_app_notification_read_time_ck CHECK (
            read_at IS NULL OR read_at >= created_at
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX in_app_notification_unread_idx "
        "ON f1.in_app_notification(enterprise_id, recipient_user_id, created_at, id) "
        "WHERE read_at IS NULL"
    )


def _wave2_state_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p2_guard_finding_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.service_case_id IS DISTINCT FROM OLD.service_case_id
             OR NEW.site_visit_id IS DISTINCT FROM OLD.site_visit_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P2_FINDING_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'open' AND NEW.status = 'rectifying')
            OR (OLD.status = 'rejected' AND NEW.status = 'rectifying')
            OR (OLD.status = 'rectifying' AND NEW.status = 'submitted')
            OR (OLD.status = 'submitted' AND NEW.status = 'reviewing')
            OR (OLD.status = 'reviewing'
                AND NEW.status IN ('passed','rejected'))
            OR (OLD.status = 'passed' AND NEW.status = 'closed')
          ) THEN
            RAISE EXCEPTION 'P2_FINDING_TRANSITION_INVALID';
          END IF;
          IF NEW.status <> OLD.status AND (
            NEW.title IS DISTINCT FROM OLD.title
            OR NEW.description IS DISTINCT FROM OLD.description
            OR NEW.severity IS DISTINCT FROM OLD.severity
            OR NEW.responsible_user_id IS DISTINCT FROM OLD.responsible_user_id
            OR NEW.due_at IS DISTINCT FROM OLD.due_at
          ) THEN
            RAISE EXCEPTION 'P2_FINDING_TRANSITION_FIELDS_IMMUTABLE';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER p2_finding_update_guard
        BEFORE UPDATE ON f1.finding
        FOR EACH ROW EXECUTE FUNCTION f1.p2_guard_finding_update()
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.p2_guard_finding_update() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.p2_guard_finding_update() TO f1_api"
    )


def _wave3_state_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p2_guard_site_visit_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.service_case_id <> OLD.service_case_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P2_SITE_VISIT_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.status = OLD.status THEN
            IF OLD.status <> 'planned'
               OR NEW.started_at IS DISTINCT FROM OLD.started_at
               OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
            THEN
              RAISE EXCEPTION 'P2_SITE_VISIT_EDIT_INVALID';
            END IF;
          ELSIF NOT (
            (OLD.status = 'planned' AND NEW.status = 'in_progress'
             AND NEW.started_at IS NOT NULL AND NEW.completed_at IS NULL)
            OR (OLD.status = 'in_progress' AND NEW.status = 'completed'
                AND NEW.started_at = OLD.started_at
                AND NEW.completed_at IS NOT NULL)
          ) THEN
            RAISE EXCEPTION 'P2_SITE_VISIT_TRANSITION_INVALID';
          END IF;
          IF NEW.status <> OLD.status AND (
            NEW.planned_start_at IS DISTINCT FROM OLD.planned_start_at
            OR NEW.planned_end_at IS DISTINCT FROM OLD.planned_end_at
          ) THEN
            RAISE EXCEPTION 'P2_SITE_VISIT_TRANSITION_FIELDS_IMMUTABLE';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER p2_site_visit_update_guard
        BEFORE UPDATE ON f1.site_visit
        FOR EACH ROW EXECUTE FUNCTION f1.p2_guard_site_visit_update()
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.p2_guard_site_visit_update() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.p2_guard_site_visit_update() TO f1_api"
    )


def _wave4_state_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p2_guard_notification_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.recipient_user_id <> OLD.recipient_user_id
             OR NEW.timeline_event_id <> OLD.timeline_event_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P2_NOTIFICATION_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.read_at IS NOT NULL
             OR NEW.read_at IS NULL
             OR NEW.read_at < OLD.created_at
          THEN
            RAISE EXCEPTION 'P2_NOTIFICATION_READ_TRANSITION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER p2_notification_update_guard
        BEFORE UPDATE ON f1.in_app_notification
        FOR EACH ROW EXECUTE FUNCTION f1.p2_guard_notification_update()
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.p2_guard_notification_update() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.p2_guard_notification_update() TO f1_api"
    )


def _row_level_security() -> None:
    for table in ("service_case", "service_assignment"):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY p2_assignment_select ON f1.service_assignment
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = service_assignment.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR (
              status IN ('pending','accepted','rejected')
              AND EXISTS (
                SELECT 1 FROM f1.user_profile AS profile
                WHERE profile.id = service_assignment.assignee_user_id
                  AND profile.keycloak_sub = f1.current_sub()
              )
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_assignment_insert ON f1.service_assignment
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'pending'
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_assignment.enterprise_id
              AND actor.user_id = service_assignment.assigned_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS target
            WHERE target.enterprise_id = service_assignment.enterprise_id
              AND target.user_id = service_assignment.assignee_user_id
              AND (
                (target.role = 'plant_admin' AND capacity = 'employee')
                OR (target.role = 'auditor' AND capacity = 'consultant')
                OR (target.role = 'partner' AND capacity = 'partner')
              )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_assignment_manager_revoke ON f1.service_assignment
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status IN ('pending','accepted')
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_assignment.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status = 'revoked'
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_assignment.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_assignment_assignee_response ON f1.service_assignment
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'pending'
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = service_assignment.assignee_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status IN ('accepted','rejected')
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = service_assignment.assignee_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_case_select ON f1.service_case
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = service_case.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = service_case.enterprise_id
                AND assignment.service_case_id = service_case.id
                AND assignment.status IN ('pending','accepted')
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_case_insert ON f1.service_case
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'planned'
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_case.enterprise_id
              AND actor.user_id = service_case.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_case_manager_update ON f1.service_case
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status NOT IN ('closed','cancelled')
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_case.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = service_case.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )


def _wave2_row_level_security() -> None:
    for table in ("site_visit", "finding", "corrective_action", "finding_review"):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY p2_site_visit_select ON f1.site_visit
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = site_visit.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = site_visit.enterprise_id
                AND assignment.service_case_id = site_visit.service_case_id
                AND assignment.status = 'accepted'
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_finding_select ON f1.finding
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = finding.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = finding.enterprise_id
                AND assignment.service_case_id = COALESCE(
                  finding.service_case_id,
                  (SELECT visit.service_case_id FROM f1.site_visit AS visit
                   WHERE visit.enterprise_id = finding.enterprise_id
                     AND visit.id = finding.site_visit_id)
                )
                AND assignment.status = 'accepted'
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_finding_insert ON f1.finding
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'open'
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = finding.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = finding.enterprise_id
              AND parent_case.id = COALESCE(
                finding.service_case_id,
                (SELECT visit.service_case_id FROM f1.site_visit AS visit
                 WHERE visit.enterprise_id = finding.enterprise_id
                   AND visit.id = finding.site_visit_id)
              )
              AND parent_case.status IN ('planned','in_progress')
          )
          AND (
            site_visit_id IS NULL OR service_case_id IS NULL OR EXISTS (
              SELECT 1 FROM f1.site_visit AS visit
              WHERE visit.enterprise_id = finding.enterprise_id
                AND visit.id = finding.site_visit_id
                AND visit.service_case_id = finding.service_case_id
            )
          )
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = finding.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = finding.enterprise_id
                AND assignment.service_case_id = COALESCE(
                  finding.service_case_id,
                  (SELECT visit.service_case_id FROM f1.site_visit AS visit
                   WHERE visit.enterprise_id = finding.enterprise_id
                     AND visit.id = finding.site_visit_id)
                )
                AND assignment.status = 'accepted'
                AND assignment.capacity IN ('employee','consultant')
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_finding_edit ON f1.finding
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'open'
          AND (
            EXISTS (
              SELECT 1
              FROM f1.enterprise_user AS actor
              JOIN f1.user_profile AS profile ON profile.id = actor.user_id
              WHERE actor.enterprise_id = finding.enterprise_id
                AND profile.keycloak_sub = f1.current_sub()
                AND actor.role IN ('super_admin','enterprise_admin')
            )
            OR EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = finding.enterprise_id
                AND assignment.service_case_id = COALESCE(
                  finding.service_case_id,
                  (SELECT visit.service_case_id FROM f1.site_visit AS visit
                   WHERE visit.enterprise_id = finding.enterprise_id
                     AND visit.id = finding.site_visit_id)
                )
                AND assignment.status = 'accepted'
                AND assignment.capacity IN ('employee','consultant')
                AND profile.keycloak_sub = f1.current_sub()
            )
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status = 'open'
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_finding_enterprise_start_rectification ON f1.finding
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status IN ('open','rejected')
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = finding.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role = 'enterprise_admin'
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status = 'rectifying'
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_finding_enterprise_submit ON f1.finding
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'rectifying'
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = finding.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role = 'enterprise_admin'
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status = 'submitted'
        )
        """
    )
    for name, old_status, new_statuses in (
        ("p2_finding_reviewer_start", "submitted", "'reviewing'"),
        ("p2_finding_reviewer_decide", "reviewing", "'passed','rejected'"),
    ):
        op.execute(
            f"""
            CREATE POLICY {name} ON f1.finding
            FOR UPDATE TO f1_api
            USING (
              enterprise_id = f1.current_enterprise_id()
              AND f1.session_authorized(enterprise_id)
              AND status = '{old_status}'
              AND (
                EXISTS (
                  SELECT 1
                  FROM f1.enterprise_user AS actor
                  JOIN f1.user_profile AS profile ON profile.id = actor.user_id
                  WHERE actor.enterprise_id = finding.enterprise_id
                    AND profile.keycloak_sub = f1.current_sub()
                    AND actor.role = 'super_admin'
                )
                OR EXISTS (
                  SELECT 1
                  FROM f1.enterprise_user AS actor
                  JOIN f1.user_profile AS profile ON profile.id = actor.user_id
                  JOIN f1.service_assignment AS assignment
                    ON assignment.enterprise_id = actor.enterprise_id
                   AND assignment.assignee_user_id = actor.user_id
                  WHERE actor.enterprise_id = finding.enterprise_id
                    AND profile.keycloak_sub = f1.current_sub()
                    AND actor.role = 'auditor'
                    AND assignment.service_case_id = COALESCE(
                      finding.service_case_id,
                      (SELECT visit.service_case_id FROM f1.site_visit AS visit
                       WHERE visit.enterprise_id = finding.enterprise_id
                         AND visit.id = finding.site_visit_id)
                    )
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
            )
            WITH CHECK (
              enterprise_id = f1.current_enterprise_id()
              AND status IN ({new_statuses})
            )
            """
        )
    op.execute(
        """
        CREATE POLICY p2_finding_manager_close ON f1.finding
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'passed'
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = finding.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status = 'closed'
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_corrective_action_select ON f1.corrective_action
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.finding AS parent
            WHERE parent.enterprise_id = corrective_action.enterprise_id
              AND parent.id = corrective_action.finding_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_corrective_action_insert ON f1.corrective_action
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = corrective_action.enterprise_id
              AND actor.user_id = corrective_action.submitted_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role = 'enterprise_admin'
          )
          AND EXISTS (
            SELECT 1 FROM f1.finding AS parent
            WHERE parent.enterprise_id = corrective_action.enterprise_id
              AND parent.id = corrective_action.finding_id
              AND parent.status = 'rectifying'
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_finding_review_select ON f1.finding_review
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.finding AS parent
            WHERE parent.enterprise_id = finding_review.enterprise_id
              AND parent.id = finding_review.finding_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_finding_review_insert ON f1.finding_review
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = finding_review.reviewer_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
          AND EXISTS (
            SELECT 1 FROM f1.finding AS parent
            WHERE parent.enterprise_id = finding_review.enterprise_id
              AND parent.id = finding_review.finding_id
              AND parent.status = 'reviewing'
              AND (
                EXISTS (
                  SELECT 1
                  FROM f1.enterprise_user AS actor
                  JOIN f1.user_profile AS profile ON profile.id = actor.user_id
                  WHERE actor.enterprise_id = parent.enterprise_id
                    AND actor.user_id = finding_review.reviewer_user_id
                    AND profile.keycloak_sub = f1.current_sub()
                    AND actor.role = 'super_admin'
                )
                OR EXISTS (
                  SELECT 1
                  FROM f1.enterprise_user AS actor
                  JOIN f1.user_profile AS profile ON profile.id = actor.user_id
                  JOIN f1.service_assignment AS assignment
                    ON assignment.enterprise_id = actor.enterprise_id
                   AND assignment.assignee_user_id = actor.user_id
                  WHERE actor.enterprise_id = parent.enterprise_id
                    AND actor.user_id = finding_review.reviewer_user_id
                    AND profile.keycloak_sub = f1.current_sub()
                    AND actor.role = 'auditor'
                    AND assignment.service_case_id = COALESCE(
                      parent.service_case_id,
                      (SELECT visit.service_case_id FROM f1.site_visit AS visit
                       WHERE visit.enterprise_id = parent.enterprise_id
                         AND visit.id = parent.site_visit_id)
                    )
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        """
    )


def _wave3_row_level_security() -> None:
    op.execute("ALTER TABLE f1.business_timeline ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.business_timeline FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY p2_case_executor_aggregate ON f1.service_case
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status IN ('planned','in_progress')
          AND EXISTS (
            SELECT 1
            FROM f1.service_assignment AS assignment
            JOIN f1.user_profile AS profile
              ON profile.id = assignment.assignee_user_id
            WHERE assignment.enterprise_id = service_case.enterprise_id
              AND assignment.service_case_id = service_case.id
              AND assignment.status = 'accepted'
              AND assignment.capacity IN ('employee','consultant')
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status IN ('in_progress','completed')
          AND EXISTS (
            SELECT 1
            FROM f1.service_assignment AS assignment
            JOIN f1.user_profile AS profile
              ON profile.id = assignment.assignee_user_id
            WHERE assignment.enterprise_id = service_case.enterprise_id
              AND assignment.service_case_id = service_case.id
              AND assignment.status = 'accepted'
              AND assignment.capacity IN ('employee','consultant')
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_site_visit_manager_insert ON f1.site_visit
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'planned'
          AND started_at IS NULL
          AND completed_at IS NULL
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = site_visit.enterprise_id
              AND actor.user_id = site_visit.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = site_visit.enterprise_id
              AND parent_case.id = site_visit.service_case_id
              AND parent_case.status IN ('planned','in_progress')
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_site_visit_manager_update ON f1.site_visit
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status IN ('planned','in_progress')
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = site_visit.enterprise_id
              AND parent_case.id = site_visit.service_case_id
              AND parent_case.status IN ('planned','in_progress')
          )
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = site_visit.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status IN ('planned','in_progress','completed')
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = site_visit.enterprise_id
              AND parent_case.id = site_visit.service_case_id
              AND parent_case.status IN ('planned','in_progress')
          )
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = site_visit.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_site_visit_executor_update ON f1.site_visit
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status IN ('planned','in_progress')
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = site_visit.enterprise_id
              AND parent_case.id = site_visit.service_case_id
              AND parent_case.status IN ('planned','in_progress')
          )
          AND EXISTS (
            SELECT 1
            FROM f1.service_assignment AS assignment
            JOIN f1.user_profile AS profile
              ON profile.id = assignment.assignee_user_id
            WHERE assignment.enterprise_id = site_visit.enterprise_id
              AND assignment.service_case_id = site_visit.service_case_id
              AND assignment.status = 'accepted'
              AND assignment.capacity IN ('employee','consultant')
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND status IN ('in_progress','completed')
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = site_visit.enterprise_id
              AND parent_case.id = site_visit.service_case_id
              AND parent_case.status IN ('planned','in_progress')
          )
          AND EXISTS (
            SELECT 1
            FROM f1.service_assignment AS assignment
            JOIN f1.user_profile AS profile
              ON profile.id = assignment.assignee_user_id
            WHERE assignment.enterprise_id = site_visit.enterprise_id
              AND assignment.service_case_id = site_visit.service_case_id
              AND assignment.status = 'accepted'
              AND assignment.capacity IN ('employee','consultant')
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p2_business_timeline_select ON f1.business_timeline
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = business_timeline.enterprise_id
              AND parent_case.id = business_timeline.service_case_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_business_timeline_insert ON f1.business_timeline
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = business_timeline.actor_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent_case
            WHERE parent_case.enterprise_id = business_timeline.enterprise_id
              AND parent_case.id = business_timeline.service_case_id
          )
        )
        """
    )


def _wave4_row_level_security() -> None:
    op.execute("ALTER TABLE f1.in_app_notification ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.in_app_notification FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY p2_notification_recipient_select ON f1.in_app_notification
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = in_app_notification.recipient_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_notification_recipient_update ON f1.in_app_notification
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND read_at IS NULL
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = in_app_notification.recipient_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND read_at IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = in_app_notification.recipient_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p2_notification_event_insert ON f1.in_app_notification
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND read_at IS NULL
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS recipient
            WHERE recipient.enterprise_id = in_app_notification.enterprise_id
              AND recipient.user_id = in_app_notification.recipient_user_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM f1.user_profile AS actor_profile
            WHERE actor_profile.id = in_app_notification.recipient_user_id
              AND actor_profile.keycloak_sub = f1.current_sub()
          )
          AND EXISTS (
            SELECT 1 FROM f1.business_timeline AS timeline
            WHERE timeline.enterprise_id = in_app_notification.enterprise_id
              AND timeline.id = in_app_notification.timeline_event_id
          )
        )
        """
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON "
        "f1.service_case, f1.service_assignment TO f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.service_case, f1.service_assignment "
        "FROM PUBLIC, f1_worker"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.site_visit TO f1_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.finding TO f1_api")
    op.execute(
        "GRANT SELECT, INSERT ON f1.corrective_action, f1.finding_review TO f1_api"
    )
    op.execute("GRANT SELECT, INSERT ON f1.business_timeline TO f1_api")
    op.execute(
        "REVOKE UPDATE, DELETE ON f1.business_timeline FROM f1_api"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON f1.in_app_notification TO f1_api"
    )
    op.execute("REVOKE DELETE ON f1.in_app_notification FROM f1_api")
    op.execute(
        "REVOKE ALL ON f1.site_visit, f1.finding, f1.corrective_action, "
        "f1.finding_review, f1.business_timeline, f1.in_app_notification "
        "FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS f1.in_app_notification CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.business_timeline CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.finding_review CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.corrective_action CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.finding CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.site_visit CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.service_assignment CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.service_case CASCADE")
    op.execute("DROP FUNCTION IF EXISTS f1.p2_guard_site_visit_update()")
    op.execute("DROP FUNCTION IF EXISTS f1.p2_guard_notification_update()")
    op.execute("DROP FUNCTION IF EXISTS f1.p2_guard_finding_update()")
    op.execute("DROP FUNCTION IF EXISTS f1.p2_guard_service_assignment_update()")
    op.execute("DROP FUNCTION IF EXISTS f1.p2_guard_service_case_update()")
