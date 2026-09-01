"""Immutable analysis-report health snapshot.

Default engineering remains on f1_0014. Material-RAG remains f1_0016.
f1_0017 is unchanged. The dedicated analysis-report migrator requests f1_0018.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1_0018"
down_revision: str | None = "f1_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "analysis_report_health_snapshot"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE f1.{_TABLE} (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_id uuid NOT NULL,
          client_account_id uuid NOT NULL,
          payload jsonb NOT NULL,
          payload_sha256 text NOT NULL,
          score integer NOT NULL,
          max_score integer NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_health_snapshot_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_health_snapshot_version_uq
            UNIQUE (enterprise_id, version_id),
          CONSTRAINT analysis_report_health_snapshot_score_ck
            CHECK (score >= 0 AND score <= 100),
          CONSTRAINT analysis_report_health_snapshot_max_ck
            CHECK (max_score = 100),
          CONSTRAINT analysis_report_health_snapshot_score_max_ck
            CHECK (score <= max_score),
          CONSTRAINT analysis_report_health_snapshot_sha_ck
            CHECK (payload_sha256 ~ '^[0-9a-f]{{64}}$'),
          CONSTRAINT analysis_report_health_snapshot_payload_object_ck
            CHECK (jsonb_typeof(payload) = 'object'),
          CONSTRAINT analysis_report_health_snapshot_version_fk
            FOREIGN KEY (enterprise_id, report_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, report_id, id),
          CONSTRAINT analysis_report_health_snapshot_client_fk
            FOREIGN KEY (enterprise_id, report_id, client_account_id)
            REFERENCES f1.analysis_report (enterprise_id, id, client_account_id)
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


def _client_readable() -> str:
    return (
        "f1.session_authorized(f1.current_enterprise_id()) "
        "AND EXISTS ("
        " SELECT 1 FROM f1.analysis_report_client_audience AS binding "
        f" WHERE binding.enterprise_id = {_TABLE}.enterprise_id "
        f" AND binding.client_account_id = {_TABLE}.client_account_id "
        " AND binding.status = 'active' "
        " AND binding.audience_enterprise_id = f1.current_enterprise_id()"
        ") "
        "AND EXISTS ("
        " SELECT 1 FROM f1.analysis_report_version AS published "
        f" WHERE published.enterprise_id = {_TABLE}.enterprise_id "
        f" AND published.id = {_TABLE}.version_id "
        " AND published.status = 'published' "
        " AND published.artifact_ready IS TRUE"
        ")"
    )


def _rls_and_grants() -> None:
    admin = _provider_admin()
    client = _client_readable()
    op.execute(f"ALTER TABLE f1.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE f1.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY analysis_report_health_snapshot_provider_select "
        f"ON f1.{_TABLE} FOR SELECT TO f1_api USING ({admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_health_snapshot_provider_insert "
        f"ON f1.{_TABLE} FOR INSERT TO f1_api WITH CHECK ("
        f"{admin} AND EXISTS ("
        " SELECT 1 FROM f1.analysis_report_version AS published "
        f" WHERE published.enterprise_id = {_TABLE}.enterprise_id "
        f" AND published.id = {_TABLE}.version_id "
        " AND published.status = 'published' "
        " AND published.artifact_ready IS TRUE"
        "))"
    )
    op.execute(
        f"CREATE POLICY analysis_report_health_snapshot_client_select "
        f"ON f1.{_TABLE} FOR SELECT TO f1_api USING ({client})"
    )
    op.execute(f"GRANT SELECT, INSERT ON f1.{_TABLE} TO f1_api")
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM f1_worker")


def _immutable_trigger() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.reject_health_snapshot_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
          RAISE EXCEPTION 'ANALYSIS_REPORT_HEALTH_SNAPSHOT_IMMUTABLE';
        END
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER analysis_report_health_snapshot_immutable "
        f"BEFORE UPDATE OR DELETE ON f1.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION f1.reject_health_snapshot_mutation()"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS f1.{_TABLE} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS f1.reject_health_snapshot_mutation()")
