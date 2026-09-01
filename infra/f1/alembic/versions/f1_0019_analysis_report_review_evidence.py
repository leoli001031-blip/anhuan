"""Persist immutable analysis-report review checklist and return reasons."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0019"
down_revision: str | None = "f1_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "analysis_report_review_event"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE f1.{_TABLE} (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_id uuid NOT NULL,
          actor_user_id uuid NOT NULL,
          action text NOT NULL,
          checklist jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          comment text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_review_event_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_review_event_action_ck
            CHECK (action IN ('submit','return','approve')),
          CONSTRAINT analysis_report_review_event_checklist_object_ck
            CHECK (jsonb_typeof(checklist) = 'object'),
          CONSTRAINT analysis_report_review_event_comment_ck
            CHECK (comment IS NULL OR char_length(comment) BETWEEN 1 AND 2000),
          CONSTRAINT analysis_report_review_event_shape_ck CHECK (
            (action = 'submit' AND checklist = '{{}}'::jsonb AND comment IS NULL)
            OR
            (action = 'return' AND checklist = '{{}}'::jsonb AND comment IS NOT NULL)
            OR
            (action = 'approve' AND checklist = jsonb_build_object(
              'citation_traceable', true,
              'risks_complete', true,
              'usage_boundary', true
            ))
          ),
          CONSTRAINT analysis_report_review_event_version_fk
            FOREIGN KEY (enterprise_id, report_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, report_id, id),
          CONSTRAINT analysis_report_review_event_actor_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user (enterprise_id, user_id)
        )
        """
    )
    _rls_and_grants()
    _immutable_trigger()


def _provider_admin() -> str:
    return (
        f"{_TABLE}.enterprise_id = f1.current_enterprise_id() "
        f"AND f1.session_authorized({_TABLE}.enterprise_id) "
        "AND EXISTS ("
        " SELECT 1 FROM f1.enterprise_user AS actor "
        " JOIN f1.user_profile AS profile ON profile.id = actor.user_id "
        f" WHERE actor.enterprise_id = {_TABLE}.enterprise_id "
        " AND profile.keycloak_sub = f1.current_sub() "
        " AND actor.role IN ('super_admin','enterprise_admin')"
        ")"
    )


def _rls_and_grants() -> None:
    admin = _provider_admin()
    op.execute(f"ALTER TABLE f1.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE f1.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY analysis_report_review_event_provider_select "
        f"ON f1.{_TABLE} FOR SELECT TO f1_api USING ({admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_review_event_provider_insert "
        f"ON f1.{_TABLE} FOR INSERT TO f1_api WITH CHECK ({admin})"
    )
    op.execute(f"GRANT SELECT, INSERT ON f1.{_TABLE} TO f1_api")
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM f1_worker")


def _immutable_trigger() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.reject_analysis_report_review_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
          RAISE EXCEPTION 'ANALYSIS_REPORT_REVIEW_EVENT_IMMUTABLE';
        END
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER analysis_report_review_event_immutable "
        f"BEFORE UPDATE OR DELETE ON f1.{_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION f1.reject_analysis_report_review_mutation()"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS f1.{_TABLE} CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS f1.reject_analysis_report_review_mutation()"
    )
