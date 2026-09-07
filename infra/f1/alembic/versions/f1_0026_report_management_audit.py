"""Report management audit: immutable archive/unarchive events.

The version-level ``analysis_report_audit_event`` guard only accepts
version-bound workflow actions (it requires ``version_id`` and an exact
from/to status pair), so report-level soft-archive operations — including
archive of an empty report with no version at all — cannot be recorded there
without weakening that guard.  This migration adds a dedicated, insert-only
``analysis_report_management_event`` table instead:

* ``f1_api`` receives SELECT + column-scoped INSERT only — no UPDATE, no
  DELETE — so events are immutable for the application role.
* A BEFORE INSERT guard re-authenticates the actor as a provider admin of
  the report's enterprise, binds the session enterprise, and proves the
  matching report write happened in the SAME transaction
  (``report.updated_at >= transaction_timestamp()``, the same technique the
  version audit guard uses).  ``report_archived`` events must match the
  archived_by/archived_reason the report row actually carries;
  ``report_unarchived`` events carry no reason and require the archive mark
  to be cleared in this transaction.

Unarchive may clear the current archived_by/at/reason columns on the report
row, but the event history stays queryable — who, when, why archived, and
who restored.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0026"
down_revision: str | None = "f1_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROVIDER_ADMIN = (
    "analysis_report_management_event.enterprise_id = "
    "f1.current_enterprise_id() "
    "AND f1.session_authorized("
    "analysis_report_management_event.enterprise_id) "
    "AND EXISTS ("
    " SELECT 1 FROM f1.enterprise_user AS actor "
    " JOIN f1.user_profile AS profile ON profile.id = actor.user_id "
    " WHERE actor.enterprise_id = "
    "analysis_report_management_event.enterprise_id "
    " AND profile.keycloak_sub = f1.current_sub() "
    " AND actor.role IN ('super_admin','enterprise_admin')"
    ")"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE f1.analysis_report_management_event (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          actor_user_id uuid NOT NULL,
          action text NOT NULL,
          reason text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_management_event_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_management_event_action_ck
            CHECK (action IN ('report_archived','report_unarchived')),
          CONSTRAINT analysis_report_management_event_reason_ck
            CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 500),
          CONSTRAINT analysis_report_management_event_report_fk
            FOREIGN KEY (enterprise_id, report_id)
            REFERENCES f1.analysis_report (enterprise_id, id),
          CONSTRAINT analysis_report_management_event_actor_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user (enterprise_id, user_id)
        )
        """
    )
    op.execute(
        "ALTER TABLE f1.analysis_report_management_event "
        "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report_management_event "
        "FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY analysis_report_management_event_select "
        "ON f1.analysis_report_management_event FOR SELECT TO f1_api "
        f"USING ({_PROVIDER_ADMIN})"
    )
    op.execute(
        "CREATE POLICY analysis_report_management_event_insert "
        "ON f1.analysis_report_management_event FOR INSERT TO f1_api "
        f"WITH CHECK ({_PROVIDER_ADMIN})"
    )
    # Insert-only for the application role: no UPDATE/DELETE grant exists,
    # making the audit trail immutable short of the migration owner.
    op.execute(
        "GRANT SELECT ON f1.analysis_report_management_event TO f1_api"
    )
    op.execute(
        "GRANT INSERT (id, enterprise_id, report_id, actor_user_id, "
        "action, reason) "
        "ON f1.analysis_report_management_event TO f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.analysis_report_management_event FROM PUBLIC"
    )
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_management_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_actor_id uuid;
        BEGIN
          SELECT membership.user_id INTO v_actor_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND membership.role IN ('super_admin','enterprise_admin');
          IF v_actor_id IS NULL
             OR NEW.actor_user_id IS DISTINCT FROM v_actor_id
             OR NULLIF(current_setting('f1.enterprise_id',true),'')::uuid
                IS DISTINCT FROM NEW.enterprise_id THEN
            RAISE EXCEPTION 'REPORT_MANAGEMENT_EVENT_ACTOR_INVALID';
          END IF;
          IF NEW.action='report_archived' THEN
            IF NOT EXISTS (
              SELECT 1 FROM f1.analysis_report AS report
              WHERE report.enterprise_id=NEW.enterprise_id
                AND report.id=NEW.report_id
                AND report.archived_at IS NOT NULL
                AND report.archived_by_user_id=NEW.actor_user_id
                AND report.archived_reason IS NOT DISTINCT FROM NEW.reason
                AND report.updated_at>=transaction_timestamp()
            ) THEN
              RAISE EXCEPTION 'REPORT_MANAGEMENT_EVENT_STATE_INVALID';
            END IF;
          ELSIF NEW.action='report_unarchived' THEN
            IF NEW.reason IS NOT NULL OR NOT EXISTS (
              SELECT 1 FROM f1.analysis_report AS report
              WHERE report.enterprise_id=NEW.enterprise_id
                AND report.id=NEW.report_id
                AND report.archived_at IS NULL
                AND report.updated_at>=transaction_timestamp()
            ) THEN
              RAISE EXCEPTION 'REPORT_MANAGEMENT_EVENT_STATE_INVALID';
            END IF;
          ELSE
            RAISE EXCEPTION 'REPORT_MANAGEMENT_EVENT_ACTION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_management_insert_guard "
        "BEFORE INSERT ON f1.analysis_report_management_event FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_management_insert()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS analysis_report_management_insert_guard "
        "ON f1.analysis_report_management_event"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.guard_analysis_report_management_insert()"
    )
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_management_event_insert "
        "ON f1.analysis_report_management_event"
    )
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_management_event_select "
        "ON f1.analysis_report_management_event"
    )
    op.execute("DROP TABLE IF EXISTS f1.analysis_report_management_event")
