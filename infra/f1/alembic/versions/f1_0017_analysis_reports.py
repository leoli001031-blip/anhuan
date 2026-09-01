"""Independent analysis-report tables, RLS, and grants.

Default engineering remains on f1_0014. Dedicated migrator requests f1_0017.
P4 business_report tables are untouched.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0017"
down_revision: str | None = "f1_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "analysis_report_client_audience",
    "analysis_report",
    "analysis_report_version",
    "analysis_report_section",
    "analysis_report_citation",
    "analysis_report_generation_job",
    "analysis_report_audit_event",
)
_STATUSES = (
    "queued,generating,draft,review_pending,changes_requested,"
    "approved,published,superseded,withdrawn,failed"
)


def upgrade() -> None:
    _tables()
    _rls_and_grants()


def _provider_admin(table: str) -> str:
    return (
        f"{table}.enterprise_id = f1.current_enterprise_id() "
        f"AND f1.session_authorized({table}.enterprise_id) "
        "AND EXISTS ("
        " SELECT 1 FROM f1.enterprise_user AS actor "
        " JOIN f1.user_profile AS profile ON profile.id = actor.user_id "
        f" WHERE actor.enterprise_id = {table}.enterprise_id "
        " AND profile.keycloak_sub = f1.current_sub() "
        " AND actor.role IN ('super_admin','enterprise_admin')"
        ")"
    )


def _client_via_binding(table: str) -> str:
    return (
        "f1.session_authorized(f1.current_enterprise_id()) "
        "AND EXISTS ("
        " SELECT 1 FROM f1.analysis_report_client_audience AS binding "
        f" WHERE binding.enterprise_id = {table}.enterprise_id "
        f" AND binding.client_account_id = {table}.client_account_id "
        " AND binding.status = 'active' "
        " AND binding.audience_enterprise_id = f1.current_enterprise_id()"
        ")"
    )


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.analysis_report_client_audience (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          client_account_id uuid NOT NULL,
          audience_enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_audience_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_audience_provider_client_uq
            UNIQUE (enterprise_id, client_account_id),
          CONSTRAINT analysis_report_audience_provider_audience_uq
            UNIQUE (enterprise_id, audience_enterprise_id),
          CONSTRAINT analysis_report_audience_status_ck
            CHECK (status IN ('active','revoked')),
          CONSTRAINT analysis_report_audience_account_fk
            FOREIGN KEY (enterprise_id, client_account_id)
            REFERENCES f1.crm_account (enterprise_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.analysis_report (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          client_account_id uuid NOT NULL,
          template_id text NOT NULL,
          title text NOT NULL,
          current_version_id uuid,
          current_version_no integer NOT NULL,
          client_visible boolean NOT NULL DEFAULT FALSE,
          create_request_id uuid NOT NULL,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_enterprise_id_id_client_uq
            UNIQUE (enterprise_id, id, client_account_id),
          CONSTRAINT analysis_report_create_request_uq
            UNIQUE (enterprise_id, create_request_id),
          CONSTRAINT analysis_report_template_ck
            CHECK (template_id = 'enterprise-ehs-material-analysis-v1'),
          CONSTRAINT analysis_report_title_ck
            CHECK (title = '企业安环资料分析报告'),
          CONSTRAINT analysis_report_version_no_ck
            CHECK (current_version_no >= 0),
          CONSTRAINT analysis_report_client_fk
            FOREIGN KEY (enterprise_id, client_account_id)
            REFERENCES f1.crm_account (enterprise_id, id),
          CONSTRAINT analysis_report_actor_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user (enterprise_id, user_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE f1.analysis_report_version (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          client_account_id uuid NOT NULL,
          version_number integer NOT NULL,
          status text NOT NULL,
          source_fingerprint_sha256 text NOT NULL,
          artifact_ready boolean NOT NULL DEFAULT FALSE,
          published_at timestamptz,
          created_by_user_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_version_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_version_enterprise_report_id_uq
            UNIQUE (enterprise_id, report_id, id),
          CONSTRAINT analysis_report_version_number_uq
            UNIQUE (enterprise_id, report_id, version_number),
          CONSTRAINT analysis_report_version_number_ck
            CHECK (version_number > 0),
          CONSTRAINT analysis_report_version_status_ck
            CHECK (status IN ('{_STATUSES.replace(",", "','")}')),
          CONSTRAINT analysis_report_version_fp_ck
            CHECK (source_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'),
          CONSTRAINT analysis_report_version_publish_ck
            CHECK (
              (status = 'published' AND artifact_ready IS TRUE
               AND published_at IS NOT NULL)
              OR (status IN ('superseded','withdrawn')
                  AND published_at IS NOT NULL)
              OR (status NOT IN ('published','superseded','withdrawn')
                  AND published_at IS NULL)
            ),
          CONSTRAINT analysis_report_version_report_fk
            FOREIGN KEY (enterprise_id, report_id)
            REFERENCES f1.analysis_report (enterprise_id, id),
          CONSTRAINT analysis_report_version_client_fk
            FOREIGN KEY (enterprise_id, client_account_id)
            REFERENCES f1.crm_account (enterprise_id, id),
          CONSTRAINT analysis_report_version_report_client_fk
            FOREIGN KEY (enterprise_id, report_id, client_account_id)
            REFERENCES f1.analysis_report (enterprise_id, id, client_account_id),
          CONSTRAINT analysis_report_version_actor_fk
            FOREIGN KEY (enterprise_id, created_by_user_id)
            REFERENCES f1.enterprise_user (enterprise_id, user_id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f1.analysis_report
          ADD CONSTRAINT analysis_report_current_version_belongs_fk
          FOREIGN KEY (enterprise_id, id, current_version_id)
          REFERENCES f1.analysis_report_version (enterprise_id, report_id, id)
          DEFERRABLE INITIALLY DEFERRED
        """
    )
    op.execute(
        """
        CREATE TABLE f1.analysis_report_section (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          version_id uuid NOT NULL,
          section_key text NOT NULL,
          title text NOT NULL,
          body text NOT NULL,
          ordinal integer NOT NULL,
          CONSTRAINT analysis_report_section_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_section_key_uq
            UNIQUE (enterprise_id, version_id, section_key),
          CONSTRAINT analysis_report_section_key_ck
            CHECK (section_key IN (
              'source_scope','status_summary','key_findings','risks_and_gaps',
              'remediation','citations','usage_boundary'
            )),
          CONSTRAINT analysis_report_section_ordinal_ck
            CHECK (ordinal BETWEEN 1 AND 7),
          CONSTRAINT analysis_report_section_body_ck
            CHECK (char_length(body) BETWEEN 1 AND 8000),
          CONSTRAINT analysis_report_section_version_fk
            FOREIGN KEY (enterprise_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.analysis_report_citation (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          version_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          document_name text NOT NULL,
          version_number integer NOT NULL,
          page_number integer NOT NULL,
          excerpt text NOT NULL,
          ordinal integer NOT NULL,
          CONSTRAINT analysis_report_citation_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_citation_page_ck
            CHECK (page_number >= 1 AND version_number >= 1),
          CONSTRAINT analysis_report_citation_excerpt_ck
            CHECK (char_length(excerpt) BETWEEN 1 AND 320),
          CONSTRAINT analysis_report_citation_version_fk
            FOREIGN KEY (enterprise_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, id),
          CONSTRAINT analysis_report_citation_doc_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version (enterprise_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.analysis_report_generation_job (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_id uuid NOT NULL,
          request_id uuid NOT NULL,
          status text NOT NULL,
          source_fingerprint_sha256 text NOT NULL,
          lease_token uuid,
          lease_until timestamptz,
          lease_owner text,
          error_reason text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_job_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_job_request_uq
            UNIQUE (enterprise_id, request_id),
          CONSTRAINT analysis_report_job_status_ck
            CHECK (status IN ('queued','generating','draft','failed')),
          CONSTRAINT analysis_report_job_fp_ck
            CHECK (source_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
          CONSTRAINT analysis_report_job_error_ck
            CHECK (error_reason IS NULL OR error_reason ~ '^[A-Z0-9_]{1,80}$'),
          CONSTRAINT analysis_report_job_owner_ck
            CHECK (lease_owner IS NULL
              OR lease_owner ~ '^[A-Za-z0-9_.:-]{1,128}$'),
          CONSTRAINT analysis_report_job_report_fk
            FOREIGN KEY (enterprise_id, report_id)
            REFERENCES f1.analysis_report (enterprise_id, id),
          CONSTRAINT analysis_report_job_version_belongs_fk
            FOREIGN KEY (enterprise_id, report_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, report_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.analysis_report_audit_event (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          version_id uuid,
          actor_user_id uuid NOT NULL,
          action text NOT NULL,
          from_status text NOT NULL,
          to_status text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT analysis_report_audit_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT analysis_report_audit_action_ck
            CHECK (action ~ '^[a-z_]{1,40}$'),
          CONSTRAINT analysis_report_audit_report_fk
            FOREIGN KEY (enterprise_id, report_id)
            REFERENCES f1.analysis_report (enterprise_id, id),
          CONSTRAINT analysis_report_audit_version_belongs_fk
            FOREIGN KEY (enterprise_id, report_id, version_id)
            REFERENCES f1.analysis_report_version (enterprise_id, report_id, id),
          CONSTRAINT analysis_report_audit_actor_fk
            FOREIGN KEY (enterprise_id, actor_user_id)
            REFERENCES f1.enterprise_user (enterprise_id, user_id)
        )
        """
    )


def _rls_and_grants() -> None:
    report_admin = _provider_admin("analysis_report")
    version_admin = _provider_admin("analysis_report_version")
    section_admin = _provider_admin("analysis_report_section")
    citation_admin = _provider_admin("analysis_report_citation")
    job_admin = _provider_admin("analysis_report_generation_job")
    audit_admin = _provider_admin("analysis_report_audit_event")
    binding_admin = _provider_admin("analysis_report_client_audience")
    published_for_client = (
        _client_via_binding("analysis_report") + " AND client_visible IS TRUE"
    )
    version_client = (
        "status = 'published' AND artifact_ready IS TRUE AND "
        + _client_via_binding("analysis_report_version")
    )
    section_client = """
      f1.session_authorized(f1.current_enterprise_id())
      AND EXISTS (
        SELECT 1 FROM f1.analysis_report_version AS published
        JOIN f1.analysis_report_client_audience AS binding
          ON binding.enterprise_id = published.enterprise_id
         AND binding.client_account_id = published.client_account_id
         AND binding.status = 'active'
         AND binding.audience_enterprise_id = f1.current_enterprise_id()
        WHERE published.enterprise_id = analysis_report_section.enterprise_id
          AND published.id = analysis_report_section.version_id
          AND published.status = 'published'
          AND published.artifact_ready IS TRUE
      )
    """
    citation_client = section_client.replace(
        "analysis_report_section", "analysis_report_citation"
    )
    generating = """
      EXISTS (
        SELECT 1 FROM f1.analysis_report_version AS live
        WHERE live.enterprise_id = analysis_report_section.enterprise_id
          AND live.id = analysis_report_section.version_id
          AND live.status = 'generating'
      )
    """
    citation_generating = generating.replace(
        "analysis_report_section", "analysis_report_citation"
    )

    for table in _TABLES:
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE POLICY analysis_report_audience_provider_select "
        "ON f1.analysis_report_client_audience FOR SELECT TO f1_api "
        f"USING ({binding_admin})"
    )
    op.execute(
        "CREATE POLICY analysis_report_audience_client_select "
        "ON f1.analysis_report_client_audience FOR SELECT TO f1_api USING ("
        "audience_enterprise_id = f1.current_enterprise_id() "
        "AND status = 'active' "
        "AND f1.session_authorized(f1.current_enterprise_id())"
        ")"
    )

    op.execute(
        f"CREATE POLICY analysis_report_provider_select ON f1.analysis_report "
        f"FOR SELECT TO f1_api USING ({report_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_client_select ON f1.analysis_report "
        f"FOR SELECT TO f1_api USING ({published_for_client})"
    )
    op.execute(
        f"""
        CREATE POLICY analysis_report_provider_insert ON f1.analysis_report
        FOR INSERT TO f1_api WITH CHECK (
          {report_admin}
          AND EXISTS (
            SELECT 1 FROM f1.crm_account AS account
            WHERE account.enterprise_id = analysis_report.enterprise_id
              AND account.id = analysis_report.client_account_id
          )
          AND EXISTS (
            SELECT 1 FROM f1.analysis_report_client_audience AS binding
            WHERE binding.enterprise_id = analysis_report.enterprise_id
              AND binding.client_account_id = analysis_report.client_account_id
              AND binding.status = 'active'
          )
        )
        """
    )
    op.execute(
        f"CREATE POLICY analysis_report_provider_update ON f1.analysis_report "
        f"FOR UPDATE TO f1_api USING ({report_admin}) WITH CHECK ({report_admin})"
    )

    op.execute(
        f"CREATE POLICY analysis_report_version_provider_select "
        f"ON f1.analysis_report_version FOR SELECT TO f1_api "
        f"USING ({version_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_version_client_select "
        f"ON f1.analysis_report_version FOR SELECT TO f1_api "
        f"USING ({version_client})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_version_insert "
        f"ON f1.analysis_report_version FOR INSERT TO f1_api WITH CHECK ("
        f"{version_admin} AND status = 'queued')"
    )
    op.execute(
        f"CREATE POLICY analysis_report_version_update "
        f"ON f1.analysis_report_version FOR UPDATE TO f1_api "
        f"USING ({version_admin}) WITH CHECK ({version_admin})"
    )

    op.execute(
        f"CREATE POLICY analysis_report_section_provider_select "
        f"ON f1.analysis_report_section FOR SELECT TO f1_api "
        f"USING ({section_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_section_client_select "
        f"ON f1.analysis_report_section FOR SELECT TO f1_api "
        f"USING ({section_client})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_section_insert "
        f"ON f1.analysis_report_section FOR INSERT TO f1_api WITH CHECK ("
        f"{section_admin} AND {generating})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_section_delete "
        f"ON f1.analysis_report_section FOR DELETE TO f1_api USING ("
        f"{section_admin} AND {generating})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_citation_provider_select "
        f"ON f1.analysis_report_citation FOR SELECT TO f1_api "
        f"USING ({citation_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_citation_client_select "
        f"ON f1.analysis_report_citation FOR SELECT TO f1_api "
        f"USING ({citation_client})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_citation_insert "
        f"ON f1.analysis_report_citation FOR INSERT TO f1_api WITH CHECK ("
        f"{citation_admin} AND {citation_generating})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_citation_delete "
        f"ON f1.analysis_report_citation FOR DELETE TO f1_api USING ("
        f"{citation_admin} AND {citation_generating})"
    )

    op.execute(
        f"CREATE POLICY analysis_report_job_select "
        f"ON f1.analysis_report_generation_job FOR SELECT TO f1_api "
        f"USING ({job_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_job_insert "
        f"ON f1.analysis_report_generation_job FOR INSERT TO f1_api "
        f"WITH CHECK ({job_admin} AND status = 'queued')"
    )
    op.execute(
        f"CREATE POLICY analysis_report_job_update "
        f"ON f1.analysis_report_generation_job FOR UPDATE TO f1_api "
        f"USING ({job_admin}) WITH CHECK ({job_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_audit_select "
        f"ON f1.analysis_report_audit_event FOR SELECT TO f1_api "
        f"USING ({audit_admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_audit_insert "
        f"ON f1.analysis_report_audit_event FOR INSERT TO f1_api "
        f"WITH CHECK ({audit_admin})"
    )

    names = ", ".join(f"f1.{table}" for table in _TABLES)
    op.execute(f"GRANT SELECT ON {names} TO f1_api")
    op.execute(
        "GRANT INSERT, UPDATE ON f1.analysis_report, "
        "f1.analysis_report_version, f1.analysis_report_generation_job "
        "TO f1_api"
    )
    op.execute("GRANT INSERT ON f1.analysis_report_audit_event TO f1_api")
    op.execute(
        "GRANT INSERT, DELETE ON f1.analysis_report_section, "
        "f1.analysis_report_citation TO f1_api"
    )
    op.execute(f"REVOKE ALL ON {names} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON {names} FROM f1_worker")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON "
        "f1.analysis_report_client_audience FROM f1_api"
    )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS f1.{table} CASCADE")
