"""Provider customer creation with immutable request identity."""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "f1_0033"
down_revision: str | None = "f1_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""ALTER TABLE f1.crm_account
      ADD COLUMN create_request_id uuid,
      ADD COLUMN create_request_payload jsonb,
      ADD CONSTRAINT crm_creation_request_pair CHECK (
        (create_request_id IS NULL) = (create_request_payload IS NULL)),
      ADD CONSTRAINT crm_creation_request_unique UNIQUE(enterprise_id,create_request_id)""")
    op.execute("""CREATE FUNCTION f1.guard_crm_creation_request() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF NEW.create_request_id IS DISTINCT FROM OLD.create_request_id OR
             NEW.create_request_payload IS DISTINCT FROM OLD.create_request_payload THEN
            RAISE EXCEPTION 'CRM_CREATION_REQUEST_IMMUTABLE';
          END IF;
        ELSIF session_user='f1_api' THEN
          -- Read committed statements may have waited behind revocation.
          -- The lock helper returns the live membership and holds it to commit.
          IF f1.lock_current_membership() IS DISTINCT FROM 'enterprise_admin' OR
             NOT EXISTS(SELECT 1 FROM f1.enterprise WHERE id=NEW.enterprise_id
               AND id=f1.current_enterprise_id() AND business_kind='service_provider') THEN
            RAISE EXCEPTION 'CRM_MANAGER_REQUIRED';
          END IF;
          IF NEW.create_request_id IS NULL THEN
            RAISE EXCEPTION 'CRM_ACCOUNT_REQUEST_ID_REQUIRED';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION f1.guard_crm_creation_request() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.guard_crm_creation_request() TO f1_api")
    op.execute("""CREATE TRIGGER crm_creation_request_guard BEFORE INSERT OR UPDATE
      ON f1.crm_account FOR EACH ROW EXECUTE FUNCTION f1.guard_crm_creation_request()""")


def downgrade() -> None:
    raise RuntimeError("CRM_CREATION_RESTORE_REQUIRED")
