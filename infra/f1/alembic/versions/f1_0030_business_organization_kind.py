"""Explicit organization identity for business-role projection.

Only unambiguous existing CRM/audience relationships are classified. Unknown
or dual-purpose legacy organizations require an explicit migration decision.
This migration does not grant new report or material permissions.
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "f1_0030"
down_revision: str | None = "f1_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE f1.enterprise ADD COLUMN business_kind text NOT NULL DEFAULT 'unconfigured' CHECK (business_kind IN ('unconfigured','service_provider','client'))")
    # Bootstrap-only bounded data migration, matching the official migrator.
    # FORCE RLS otherwise hides legacy audience rows from the DDL owner.
    op.execute("RESET ROLE")
    op.execute("DO $$ BEGIN IF session_user <> 'f0d_bootstrap' THEN RAISE EXCEPTION 'BUSINESS_ORGANIZATION_BOOTSTRAP_REQUIRED'; END IF; END $$")
    op.execute("""
      WITH providers AS (
        SELECT enterprise_id AS id FROM f1.crm_account
        UNION SELECT enterprise_id FROM f1.analysis_report_client_audience
      ), clients AS (
        SELECT audience_enterprise_id AS id FROM f1.analysis_report_client_audience
      )
      UPDATE f1.enterprise AS enterprise SET business_kind = CASE
        WHEN EXISTS (SELECT 1 FROM providers WHERE id=enterprise.id)
          AND NOT EXISTS (SELECT 1 FROM clients WHERE id=enterprise.id) THEN 'service_provider'
        WHEN EXISTS (SELECT 1 FROM clients WHERE id=enterprise.id)
          AND NOT EXISTS (SELECT 1 FROM providers WHERE id=enterprise.id) THEN 'client'
        ELSE 'unconfigured' END
    """)
    op.execute("SET LOCAL ROLE f0d_migration")
    op.execute("""
      CREATE FUNCTION f1.guard_business_organization_kind() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      BEGIN
        IF NEW.business_kind IS DISTINCT FROM OLD.business_kind
           AND session_user <> 'f0d_bootstrap' THEN
          RAISE EXCEPTION 'BUSINESS_ORGANIZATION_KIND_IMMUTABLE';
        END IF;
        RETURN NEW;
      END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION f1.guard_business_organization_kind() FROM PUBLIC")
    op.execute("CREATE TRIGGER business_organization_kind_immutable BEFORE UPDATE ON f1.enterprise FOR EACH ROW EXECUTE FUNCTION f1.guard_business_organization_kind()")


def downgrade() -> None:
    raise RuntimeError("BUSINESS_ORGANIZATION_RESTORE_REQUIRED")
