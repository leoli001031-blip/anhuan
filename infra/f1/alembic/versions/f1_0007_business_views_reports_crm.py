"""P4 role views, internal CRM, and immutable business report snapshots."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0007"
down_revision: str | None = "f1_0006"
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
        CREATE TABLE f1.crm_account (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          display_name text NOT NULL CHECK (
            char_length(display_name) BETWEEN 1 AND 200
          ),
          stage text NOT NULL DEFAULT 'lead' CHECK (
            stage IN ('lead','active','dormant','closed')
          ),
          owner_user_id uuid,
          industry_note text CHECK (
            industry_note IS NULL OR char_length(industry_note) <= 2000
          ),
          region_note text CHECK (
            region_note IS NULL OR char_length(region_note) <= 2000
          ),
          next_follow_up_at timestamptz,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT crm_account_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT crm_account_owner_enterprise_fk
            FOREIGN KEY (enterprise_id, owner_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT crm_account_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.crm_contact (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          account_id uuid NOT NULL,
          display_name text NOT NULL CHECK (
            char_length(display_name) BETWEEN 1 AND 200
          ),
          role_title text CHECK (
            role_title IS NULL OR char_length(role_title) <= 200
          ),
          email text CHECK (email IS NULL OR char_length(email) <= 320),
          phone text CHECK (phone IS NULL OR char_length(phone) <= 64),
          status text NOT NULL DEFAULT 'active' CHECK (
            status IN ('active','inactive')
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT crm_contact_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT crm_contact_account_enterprise_fk
            FOREIGN KEY (enterprise_id, account_id)
            REFERENCES f1.crm_account(enterprise_id, id),
          CONSTRAINT crm_contact_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.crm_follow_up (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          account_id uuid NOT NULL,
          channel text NOT NULL CHECK (
            channel IN ('onsite','meeting','phone','internal_note')
          ),
          summary text NOT NULL CHECK (
            char_length(summary) BETWEEN 1 AND 4000
          ),
          next_action text CHECK (
            next_action IS NULL OR char_length(next_action) <= 2000
          ),
          next_due_at timestamptz,
          occurred_at timestamptz NOT NULL,
          actor_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT crm_follow_up_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT crm_follow_up_account_enterprise_fk
            FOREIGN KEY (enterprise_id, account_id)
            REFERENCES f1.crm_account(enterprise_id, id),
          CONSTRAINT crm_follow_up_actor_enterprise_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.business_report (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          service_case_id uuid NOT NULL,
          title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          status text NOT NULL DEFAULT 'active' CHECK (
            status IN ('active','archived')
          ),
          current_version_no integer NOT NULL DEFAULT 0 CHECK (
            current_version_no >= 0
          ),
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT business_report_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT business_report_case_enterprise_fk
            FOREIGN KEY (enterprise_id, service_case_id)
            REFERENCES f1.service_case(enterprise_id, id),
          CONSTRAINT business_report_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.business_report_version (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_number integer NOT NULL CHECK (version_number > 0),
          lifecycle text NOT NULL DEFAULT 'current' CHECK (
            lifecycle IN ('current','superseded','void')
          ),
          change_note text CHECK (
            change_note IS NULL OR char_length(change_note) <= 2000
          ),
          canonical_snapshot jsonb NOT NULL CHECK (
            jsonb_typeof(canonical_snapshot) = 'object'
          ),
          snapshot_sha256 text NOT NULL CHECK (
            snapshot_sha256 ~ '^[0-9a-f]{64}$'
          ),
          snapshot_size_bytes bigint NOT NULL CHECK (
            snapshot_size_bytes BETWEEN 2 AND 4194304
          ),
          source_counts jsonb NOT NULL CHECK (
            jsonb_typeof(source_counts) = 'object'
          ),
          created_by_user_id uuid NOT NULL,
          captured_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT business_report_version_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT business_report_version_number_uq
            UNIQUE (enterprise_id, report_id, version_number),
          CONSTRAINT business_report_version_report_enterprise_fk
            FOREIGN KEY (enterprise_id, report_id)
            REFERENCES f1.business_report(enterprise_id, id),
          CONSTRAINT business_report_version_creator_enterprise_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX business_report_version_current_uq
        ON f1.business_report_version(enterprise_id, report_id)
        WHERE lifecycle = 'current'
        """
    )
    op.execute(
        """
        CREATE TABLE f1.business_report_artifact (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_version_id uuid NOT NULL,
          artifact_kind text NOT NULL DEFAULT 'canonical_json' CHECK (
            artifact_kind = 'canonical_json'
          ),
          storage_kind text NOT NULL DEFAULT 'database_snapshot' CHECK (
            storage_kind = 'database_snapshot'
          ),
          content_type text NOT NULL DEFAULT 'application/json' CHECK (
            content_type = 'application/json'
          ),
          status text NOT NULL DEFAULT 'ready' CHECK (status = 'ready'),
          sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
          size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 2 AND 4194304),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT business_report_artifact_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT business_report_artifact_version_uq
            UNIQUE (enterprise_id, report_version_id),
          CONSTRAINT business_report_artifact_version_enterprise_fk
            FOREIGN KEY (enterprise_id, report_version_id)
            REFERENCES f1.business_report_version(enterprise_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX crm_account_stage_idx "
        "ON f1.crm_account(enterprise_id, stage, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX crm_account_follow_up_idx "
        "ON f1.crm_account(enterprise_id, next_follow_up_at)"
    )
    op.execute(
        "CREATE INDEX crm_follow_up_account_idx "
        "ON f1.crm_follow_up(enterprise_id, account_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX business_report_case_idx "
        "ON f1.business_report(enterprise_id, service_case_id, updated_at DESC)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p4_guard_crm_account_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P4_CRM_ACCOUNT_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.stage = 'closed' AND NEW.stage <> 'closed' THEN
            RAISE EXCEPTION 'P4_CRM_ACCOUNT_CLOSED';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p4_guard_crm_contact_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.account_id <> OLD.account_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P4_CRM_CONTACT_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.status = 'inactive' AND NEW.status <> 'inactive' THEN
            RAISE EXCEPTION 'P4_CRM_CONTACT_INACTIVE';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p4_guard_business_report_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          actor_is_manager boolean;
          actor_is_consultant boolean;
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.service_case_id <> OLD.service_case_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
          THEN
            RAISE EXCEPTION 'P4_REPORT_IDENTITY_IMMUTABLE';
          END IF;
          IF OLD.status = 'archived' AND NEW.status <> 'archived' THEN
            RAISE EXCEPTION 'P4_REPORT_ARCHIVED';
          END IF;
          IF NEW.current_version_no < OLD.current_version_no
             OR NEW.current_version_no > OLD.current_version_no + 1
          THEN
            RAISE EXCEPTION 'P4_REPORT_VERSION_SEQUENCE_INVALID';
          END IF;
          IF NEW.status <> OLD.status
             AND NEW.current_version_no <> OLD.current_version_no
          THEN
            RAISE EXCEPTION 'P4_REPORT_UPDATE_AMBIGUOUS';
          END IF;
          SELECT EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = OLD.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          ) INTO actor_is_manager;
          IF NOT actor_is_manager THEN
            SELECT EXISTS (
              SELECT 1
              FROM f1.service_assignment AS assignment
              JOIN f1.user_profile AS profile
                ON profile.id = assignment.assignee_user_id
              WHERE assignment.enterprise_id = OLD.enterprise_id
                AND assignment.service_case_id = OLD.service_case_id
                AND assignment.status = 'accepted'
                AND assignment.capacity = 'consultant'
                AND profile.keycloak_sub = f1.current_sub()
            ) INTO actor_is_consultant;
            IF NOT actor_is_consultant
               OR NEW.title IS DISTINCT FROM OLD.title
               OR NEW.status IS DISTINCT FROM OLD.status
               OR NEW.current_version_no <> OLD.current_version_no + 1
            THEN
              RAISE EXCEPTION 'P4_REPORT_MANAGER_EDIT_REQUIRED';
            END IF;
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p4_guard_report_version_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.report_id <> OLD.report_id
             OR NEW.version_number <> OLD.version_number
             OR NEW.change_note IS DISTINCT FROM OLD.change_note
             OR NEW.canonical_snapshot IS DISTINCT FROM OLD.canonical_snapshot
             OR NEW.snapshot_sha256 <> OLD.snapshot_sha256
             OR NEW.snapshot_size_bytes <> OLD.snapshot_size_bytes
             OR NEW.source_counts IS DISTINCT FROM OLD.source_counts
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.captured_at <> OLD.captured_at
          THEN
            RAISE EXCEPTION 'P4_REPORT_VERSION_IMMUTABLE';
          END IF;
          IF OLD.lifecycle <> 'current'
             OR NEW.lifecycle NOT IN ('superseded','void')
          THEN
            RAISE EXCEPTION 'P4_REPORT_VERSION_TRANSITION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.p4_guard_report_artifact_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          parent_sha text;
          parent_size bigint;
        BEGIN
          SELECT snapshot_sha256, snapshot_size_bytes
          INTO parent_sha, parent_size
          FROM f1.business_report_version
          WHERE enterprise_id = NEW.enterprise_id
            AND id = NEW.report_version_id;
          IF parent_sha IS NULL
             OR NEW.sha256 <> parent_sha
             OR NEW.size_bytes <> parent_size
          THEN
            RAISE EXCEPTION 'P4_REPORT_ARTIFACT_MISMATCH';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER p4_crm_account_update_guard "
        "BEFORE UPDATE ON f1.crm_account FOR EACH ROW "
        "EXECUTE FUNCTION f1.p4_guard_crm_account_update()"
    )
    op.execute(
        "CREATE TRIGGER p4_crm_contact_update_guard "
        "BEFORE UPDATE ON f1.crm_contact FOR EACH ROW "
        "EXECUTE FUNCTION f1.p4_guard_crm_contact_update()"
    )
    op.execute(
        "CREATE TRIGGER p4_business_report_update_guard "
        "BEFORE UPDATE ON f1.business_report FOR EACH ROW "
        "EXECUTE FUNCTION f1.p4_guard_business_report_update()"
    )
    op.execute(
        "CREATE TRIGGER p4_report_version_update_guard "
        "BEFORE UPDATE ON f1.business_report_version FOR EACH ROW "
        "EXECUTE FUNCTION f1.p4_guard_report_version_update()"
    )
    op.execute(
        "CREATE TRIGGER p4_report_artifact_insert_guard "
        "BEFORE INSERT ON f1.business_report_artifact FOR EACH ROW "
        "EXECUTE FUNCTION f1.p4_guard_report_artifact_insert()"
    )
    for signature in (
        "f1.p4_guard_crm_account_update()",
        "f1.p4_guard_crm_contact_update()",
        "f1.p4_guard_business_report_update()",
        "f1.p4_guard_report_version_update()",
        "f1.p4_guard_report_artifact_insert()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _row_level_security() -> None:
    for table in (
        "crm_account",
        "crm_contact",
        "crm_follow_up",
        "business_report",
        "business_report_version",
        "business_report_artifact",
    ):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY p4_crm_account_select ON f1.crm_account
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = crm_account.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR actor.user_id = crm_account.owner_user_id
              )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_crm_account_insert ON f1.crm_account
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = crm_account.enterprise_id
              AND actor.user_id = crm_account.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_crm_account_update ON f1.crm_account
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = crm_account.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR actor.user_id = crm_account.owner_user_id
              )
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = crm_account.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR actor.user_id = crm_account.owner_user_id
              )
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p4_crm_contact_select ON f1.crm_contact
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_contact.enterprise_id
              AND parent.id = crm_contact.account_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_crm_contact_insert ON f1.crm_contact
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_contact.enterprise_id
              AND parent.id = crm_contact.account_id
          )
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = crm_contact.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_crm_contact_update ON f1.crm_contact
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_contact.enterprise_id
              AND parent.id = crm_contact.account_id
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_contact.enterprise_id
              AND parent.id = crm_contact.account_id
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p4_crm_follow_up_select ON f1.crm_follow_up
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_follow_up.enterprise_id
              AND parent.id = crm_follow_up.account_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_crm_follow_up_insert ON f1.crm_follow_up
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS parent
            WHERE parent.enterprise_id = crm_follow_up.enterprise_id
              AND parent.id = crm_follow_up.account_id
          )
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS profile
            WHERE profile.id = crm_follow_up.actor_user_id
              AND profile.keycloak_sub = f1.current_sub()
          )
        )
        """
    )

    report_access = """
      enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized(enterprise_id)
      AND (
        EXISTS (
          SELECT 1
          FROM f1.enterprise_user AS actor
          JOIN f1.user_profile AS profile ON profile.id = actor.user_id
          WHERE actor.enterprise_id = business_report.enterprise_id
            AND profile.keycloak_sub = f1.current_sub()
            AND actor.role IN ('super_admin','enterprise_admin')
        )
        OR EXISTS (
          SELECT 1
          FROM f1.service_assignment AS assignment
          JOIN f1.user_profile AS profile
            ON profile.id = assignment.assignee_user_id
          WHERE assignment.enterprise_id = business_report.enterprise_id
            AND assignment.service_case_id = business_report.service_case_id
            AND assignment.status = 'accepted'
            AND profile.keycloak_sub = f1.current_sub()
        )
      )
    """
    op.execute(
        f"""
        CREATE POLICY p4_report_select ON f1.business_report
        FOR SELECT TO f1_api USING ({report_access})
        """
    )
    op.execute(
        """
        CREATE POLICY p4_report_insert ON f1.business_report
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND status = 'active'
          AND current_version_no = 0
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = business_report.enterprise_id
              AND actor.user_id = business_report.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS parent
            WHERE parent.enterprise_id = business_report.enterprise_id
              AND parent.id = business_report.service_case_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY p4_report_update ON f1.business_report
        FOR UPDATE TO f1_api
        USING ({report_access})
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = business_report.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR EXISTS (
                  SELECT 1 FROM f1.service_assignment AS assignment
                  WHERE assignment.enterprise_id = business_report.enterprise_id
                    AND assignment.service_case_id = business_report.service_case_id
                    AND assignment.assignee_user_id = actor.user_id
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p4_report_version_select ON f1.business_report_version
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.business_report AS parent
            WHERE parent.enterprise_id = business_report_version.enterprise_id
              AND parent.id = business_report_version.report_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_report_version_insert ON f1.business_report_version
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND lifecycle = 'current'
          AND EXISTS (
            SELECT 1
            FROM f1.business_report AS parent
            JOIN f1.enterprise_user AS actor
              ON actor.enterprise_id = parent.enterprise_id
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE parent.enterprise_id = business_report_version.enterprise_id
              AND parent.id = business_report_version.report_id
              AND parent.status = 'active'
              AND actor.user_id = business_report_version.created_by_user_id
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR EXISTS (
                  SELECT 1 FROM f1.service_assignment AS assignment
                  WHERE assignment.enterprise_id = parent.enterprise_id
                    AND assignment.service_case_id = parent.service_case_id
                    AND assignment.assignee_user_id = actor.user_id
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_report_version_update ON f1.business_report_version
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND lifecycle = 'current'
          AND EXISTS (
            SELECT 1
            FROM f1.business_report AS parent
            JOIN f1.enterprise_user AS actor
              ON actor.enterprise_id = parent.enterprise_id
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE parent.enterprise_id = business_report_version.enterprise_id
              AND parent.id = business_report_version.report_id
              AND parent.status = 'active'
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR EXISTS (
                  SELECT 1 FROM f1.service_assignment AS assignment
                  WHERE assignment.enterprise_id = parent.enterprise_id
                    AND assignment.service_case_id = parent.service_case_id
                    AND assignment.assignee_user_id = actor.user_id
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND lifecycle IN ('superseded','void')
          AND EXISTS (
            SELECT 1
            FROM f1.business_report AS parent
            JOIN f1.enterprise_user AS actor
              ON actor.enterprise_id = parent.enterprise_id
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE parent.enterprise_id = business_report_version.enterprise_id
              AND parent.id = business_report_version.report_id
              AND parent.status = 'active'
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR EXISTS (
                  SELECT 1 FROM f1.service_assignment AS assignment
                  WHERE assignment.enterprise_id = parent.enterprise_id
                    AND assignment.service_case_id = parent.service_case_id
                    AND assignment.assignee_user_id = actor.user_id
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY p4_report_artifact_select ON f1.business_report_artifact
        FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.business_report_version AS parent
            WHERE parent.enterprise_id = business_report_artifact.enterprise_id
              AND parent.id = business_report_artifact.report_version_id
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY p4_report_artifact_insert ON f1.business_report_artifact
        FOR INSERT TO f1_api
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1
            FROM f1.business_report_version AS version
            JOIN f1.business_report AS report
              ON report.enterprise_id = version.enterprise_id
             AND report.id = version.report_id
            JOIN f1.enterprise_user AS actor
              ON actor.enterprise_id = report.enterprise_id
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE version.enterprise_id = business_report_artifact.enterprise_id
              AND version.id = business_report_artifact.report_version_id
              AND version.lifecycle = 'current'
              AND report.status = 'active'
              AND profile.keycloak_sub = f1.current_sub()
              AND (
                actor.role IN ('super_admin','enterprise_admin')
                OR EXISTS (
                  SELECT 1 FROM f1.service_assignment AS assignment
                  WHERE assignment.enterprise_id = report.enterprise_id
                    AND assignment.service_case_id = report.service_case_id
                    AND assignment.assignee_user_id = actor.user_id
                    AND assignment.capacity = 'consultant'
                    AND assignment.status = 'accepted'
                )
              )
          )
        )
        """
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON "
        "f1.crm_account, f1.crm_contact, f1.business_report, "
        "f1.business_report_version TO f1_api"
    )
    op.execute(
        "GRANT SELECT, INSERT ON "
        "f1.crm_follow_up, f1.business_report_artifact TO f1_api"
    )
    op.execute(
        "REVOKE DELETE ON f1.crm_account, f1.crm_contact, "
        "f1.business_report, f1.business_report_version, "
        "f1.crm_follow_up, f1.business_report_artifact FROM f1_api"
    )
    op.execute(
        "REVOKE UPDATE ON f1.crm_follow_up, f1.business_report_artifact "
        "FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.crm_account, f1.crm_contact, f1.crm_follow_up, "
        "f1.business_report, f1.business_report_version, "
        "f1.business_report_artifact FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    for table in (
        "crm_account",
        "crm_contact",
        "crm_follow_up",
        "business_report",
        "business_report_version",
        "business_report_artifact",
    ):
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $p4_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.crm_account LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.crm_contact LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.crm_follow_up LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.business_report LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.business_report_version LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.business_report_artifact LIMIT 1)
          THEN
            RAISE EXCEPTION 'P4_DOWNGRADE_REQUIRES_EMPTY_SCOPE';
          END IF;
        END
        $p4_downgrade$
        """
    )
    op.execute("DROP TABLE f1.business_report_artifact")
    op.execute("DROP TABLE f1.business_report_version")
    op.execute("DROP TABLE f1.business_report")
    op.execute("DROP TABLE f1.crm_follow_up")
    op.execute("DROP TABLE f1.crm_contact")
    op.execute("DROP TABLE f1.crm_account")
    op.execute("DROP FUNCTION f1.p4_guard_report_artifact_insert()")
    op.execute("DROP FUNCTION f1.p4_guard_report_version_update()")
    op.execute("DROP FUNCTION f1.p4_guard_business_report_update()")
    op.execute("DROP FUNCTION f1.p4_guard_crm_contact_update()")
    op.execute("DROP FUNCTION f1.p4_guard_crm_account_update()")
