"""F1 tenant boundaries + persistent workflow tables.

Adds the persistent ``upload_task`` / ``outbox`` / ``qa_request`` /
``invite_jti`` tables, tenant binding, idempotency keys, an append-only
audit, FORCE RLS on every tenant table, a composite FK that blocks
cross-enterprise plant/document references, and low-privilege grants for
the ``f1_api`` / ``f1_worker`` roles (created by ``infra/f1/roles.sql``
before this migration runs).

Every transaction sets a transaction-local tenant context from the
authenticated OIDC ``sub`` (resolved via ``f1.resolve_enterprise_for_sub``);
RLS scopes reads/writes to that enterprise.  No migration role is ever used
by the API or worker.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0002"
down_revision: str | None = "f1_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _context_functions()
    _append_only_audit()
    _composite_plant_fk()
    _workflow_tables()
    _enterprise_mapping()
    _membership_resolution()
    _fixture_bridge()
    _force_rls()
    _grants()


def _context_functions() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.current_enterprise_id()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
          WITH setting(value) AS (
            SELECT current_setting('f1.enterprise_id', true)
          )
          SELECT CASE
            WHEN value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN CAST(value AS uuid)
            ELSE NULL
          END
          FROM setting
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.current_sub()
        RETURNS text LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
          SELECT NULLIF(current_setting('f1.sub', true), '')::text
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.context_session_authorized(p_enterprise_id uuid)
        RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
          SELECT p_enterprise_id = f1.current_enterprise_id()
        $$
        """
    )


def _append_only_audit() -> None:
    op.execute("ALTER TABLE f1.audit_log ADD COLUMN enterprise_id uuid REFERENCES f1.enterprise(id)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.reject_immutable_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            RAISE EXCEPTION 'AUDIT_LOG_IMMUTABLE';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON f1.audit_log "
        "FOR EACH ROW EXECUTE FUNCTION f1.reject_immutable_audit_mutation()"
    )


def _composite_plant_fk() -> None:
    # plant(enterprise_id, id) must be unique for the composite FK.
    op.execute("ALTER TABLE f1.plant ADD CONSTRAINT plant_enterprise_id_uq UNIQUE (enterprise_id, id)")
    # Drop the old non-composite FK on document.plant_id.
    op.execute("ALTER TABLE f1.document DROP CONSTRAINT IF EXISTS document_plant_id_fkey")
    op.execute(
        """
        ALTER TABLE f1.document
          ADD CONSTRAINT document_plant_enterprise_fk
          FOREIGN KEY (enterprise_id, plant_id)
          REFERENCES f1.plant(enterprise_id, id)
        """
    )


def _workflow_tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.upload_task (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_id uuid REFERENCES f1.document(id),
          object_key text NOT NULL,
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          status text NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','scanning','indexing','done','failed'
          )),
          attempt int NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          lease_until timestamptz,
          error_reason text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT upload_task_sha_idem_uq UNIQUE (enterprise_id, content_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.outbox (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          task_id uuid NOT NULL REFERENCES f1.upload_task(id),
          event_type text NOT NULL CHECK (event_type IN (
            'upload.dispatched','upload.indexing','upload.indexed','upload.failed'
          )),
          state text NOT NULL DEFAULT 'pending' CHECK (state IN (
            'pending','dispatched','acked'
          )),
          payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          dispatched_at timestamptz,
          acked_at timestamptz,
          CONSTRAINT outbox_task_idem_uq UNIQUE (task_id, event_type)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.qa_request (
          request_id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          question_sha256 text NOT NULL CHECK (question_sha256 ~ '^[0-9a-f]{64}$'),
          status text NOT NULL DEFAULT 'accepted' CHECK (status IN (
            'accepted','done','refused'
          )),
          refusal_reason text,
          response_encrypted bytea,
          response_sha256 text CHECK (response_sha256 ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          completed_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.invite_jti (
          jti text PRIMARY KEY CHECK (length(jti) BETWEEN 8 AND 128),
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          email text NOT NULL CHECK (length(email) BETWEEN 3 AND 320),
          role text NOT NULL CHECK (role IN (
            'enterprise_admin','plant_admin','partner','auditor'
          )),
          expires_at timestamptz NOT NULL,
          consumed_by_sub text,
          consumed_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT invite_single_use_ck CHECK (
            (consumed_by_sub IS NULL) = (consumed_at IS NULL)
          )
        )
        """
    )


def _enterprise_mapping() -> None:
    # Maps an f1 enterprise to its read-only F0-I tenant (the fixture data
    # owner).  Synthetic tenants (B and any future tenant) keep NULL and are
    # never eligible to index F0-I fixture chunks.
    op.execute("ALTER TABLE f1.enterprise ADD COLUMN f0i_enterprise_id uuid")


def _membership_resolution() -> None:
    # Resolve the enterprise(s) an authenticated OIDC ``sub`` belongs to.
    # SECURITY DEFINER: the API role has no direct SELECT on enterprise_user,
    # so membership lists are never exposed through it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.resolve_enterprise_for_sub(p_sub text)
        RETURNS TABLE(enterprise_id uuid, name text, role text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f1 AS $$
          SELECT e.id, e.name, eu.role
          FROM f1.user_profile AS up
          JOIN f1.enterprise_user AS eu
            ON eu.user_id = up.id
          JOIN f1.enterprise AS e
            ON e.id = eu.enterprise_id
          WHERE up.keycloak_sub = p_sub
          ORDER BY e.id
        $$
        """
    )


def _fixture_bridge() -> None:
    # Read-only bridge into the frozen F0-I corpus.  The worker never holds
    # the migration role: this SECURITY DEFINER function (owned by
    # f0d_migration) looks up the tenant's active local-fixture session, sets
    # the f0d RLS context, and reports whether a SHA is a registered fixture.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.fixture_scope_for_sha(
          p_f0i_enterprise_id uuid, p_sha256 text
        )
        RETURNS TABLE(document_scope_id uuid, document_type text, chunk_count bigint)
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f1, f0d, f0i AS $$
        DECLARE
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = p_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC
           LIMIT 1;
          IF v_actor_id IS NULL THEN
            RETURN;
          END IF;
          PERFORM set_config('f0d.enterprise_id', p_f0i_enterprise_id::text, true);
          PERFORM set_config('f0d.actor_id', v_actor_id::text, true);
          PERFORM set_config('f0d.session_token_sha256', v_token_sha256, true);
          RETURN QUERY
            SELECT d.id, d.document_type, count(c.id)::bigint
              FROM f0i.document_scope AS d
              LEFT JOIN f0i.chunk AS c
                ON c.enterprise_id = d.enterprise_id
               AND c.document_scope_id = d.id
             WHERE d.enterprise_id = p_f0i_enterprise_id
               AND d.source_object_sha256 = p_sha256
             GROUP BY d.id, d.document_type;
        END
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION f1.fixture_scope_for_sha(uuid, text) TO f1_api, f1_worker")
    # Read-only chunk bridge: returns decrypted CHILD chunks for a registered
    # SHA under the tenant's f0d session context.  The pgp key is passed per
    # call (never stored in the function); callers only reach it through the
    # RLS-protected f1.enterprise.f0i_enterprise_id mapping.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.fixture_chunks(
          p_f0i_enterprise_id uuid, p_sha256 text, p_key bytea, p_cipher_options text
        )
        RETURNS TABLE(
          chunk_id uuid, parent_chunk_id uuid, document_id uuid, tenant_id uuid,
          kind text, char_count bigint, pages int[], body bytea
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f1, f0d, f0i, f0f_crypto AS $$
        DECLARE
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = p_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC
           LIMIT 1;
          IF v_actor_id IS NULL THEN
            RETURN;
          END IF;
          PERFORM set_config('f0d.enterprise_id', p_f0i_enterprise_id::text, true);
          PERFORM set_config('f0d.actor_id', v_actor_id::text, true);
          PERFORM set_config('f0d.session_token_sha256', v_token_sha256, true);
          RETURN QUERY
            SELECT c.id, c.parent_chunk_id, c.document_scope_id, c.enterprise_id,
                   d.document_type, c.body_plaintext_character_count::bigint,
                   ARRAY(
                     SELECT DISTINCT b.page_no
                       FROM f0i.chunk_block_link AS link
                       JOIN f0i.block AS b
                         ON b.enterprise_id = link.enterprise_id
                        AND b.id = link.block_id
                      WHERE link.enterprise_id = c.enterprise_id
                        AND link.chunk_id = c.id
                        AND b.page_no IS NOT NULL
                      ORDER BY 1
                   ),
                   f0f_crypto.pgp_sym_decrypt_bytea(
                     c.body_ciphertext, encode(p_key, 'hex'), p_cipher_options
                   )
            FROM f0i.chunk AS c
            JOIN f0i.document_scope AS d
              ON d.enterprise_id = c.enterprise_id
             AND d.id = c.document_scope_id
           WHERE c.enterprise_id = p_f0i_enterprise_id
             AND c.chunk_level = 'CHILD'
             AND d.source_object_sha256 = p_sha256
           ORDER BY c.chunk_ordinal, c.id;
        END
        $$
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.fixture_chunks(uuid, text, bytea, text) "
        "TO f1_worker"
    )
    # Read-only citation bridge: re-verifies candidate chunk IDs under the
    # tenant's f0d session context and returns decrypted bodies + pages.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.verify_citations(
          p_f0i_enterprise_id uuid, p_chunk_ids uuid[], p_key bytea, p_cipher_options text
        )
        RETURNS TABLE(
          chunk_id uuid, document_id uuid, tenant_id uuid, pages int[],
          body_sha256 text, body bytea
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f1, f0d, f0i, f0f_crypto AS $$
        DECLARE
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = p_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC LIMIT 1;
          IF v_actor_id IS NULL THEN RETURN; END IF;
          PERFORM set_config('f0d.enterprise_id', p_f0i_enterprise_id::text, true);
          PERFORM set_config('f0d.actor_id', v_actor_id::text, true);
          PERFORM set_config('f0d.session_token_sha256', v_token_sha256, true);
          RETURN QUERY
            SELECT c.id, c.document_scope_id, c.enterprise_id,
                   ARRAY(
                     SELECT DISTINCT b.page_no FROM f0i.chunk_block_link AS link
                     JOIN f0i.block AS b ON b.enterprise_id = link.enterprise_id AND b.id = link.block_id
                     WHERE link.enterprise_id = c.enterprise_id AND link.chunk_id = c.id AND b.page_no IS NOT NULL
                     ORDER BY 1
                   ),
                   c.body_plaintext_sha256::text,
                   f0f_crypto.pgp_sym_decrypt_bytea(
                     c.body_ciphertext, encode(p_key, 'hex'), p_cipher_options
                   )
            FROM f0i.chunk AS c
           WHERE c.enterprise_id = p_f0i_enterprise_id
             AND c.chunk_level = 'CHILD'
             AND c.id = ANY(p_chunk_ids);
        END
        $$
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.verify_citations(uuid, uuid[], bytea, text) "
        "TO f1_api, f1_worker"
    )
    # Worker resolves a task's enterprise (RLS-bypassing) so it can scope the
    # claim; the job on the wire carries only the task_id.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.task_enterprise(p_task_id uuid)
        RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f1 AS $$
          SELECT enterprise_id FROM f1.upload_task WHERE id = p_task_id
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION f1.task_enterprise(uuid) TO f1_worker")


def _force_rls() -> None:
    _TENANT_TABLES = (
        "enterprise",
        "plant",
        "document",
        "audit_log",
        "upload_task",
        "outbox",
        "qa_request",
        "invite_jti",
    )
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")
        # enterprise.pk: id IS the tenant key.
        if table == "enterprise":
            op.execute(
                f"""
                CREATE POLICY tenant_boundary ON f1.{table}
                FOR ALL TO f1_api, f1_worker
                USING (id = f1.current_enterprise_id())
                WITH CHECK (id = f1.current_enterprise_id())
                """
            )
        else:
            op.execute(
                f"""
                CREATE POLICY tenant_boundary ON f1.{table}
                FOR ALL TO f1_api, f1_worker
                USING (
                  enterprise_id = f1.current_enterprise_id()
                  AND f1.context_session_authorized(enterprise_id)
                )
                WITH CHECK (
                  enterprise_id = f1.current_enterprise_id()
                  AND f1.context_session_authorized(enterprise_id)
                )
                """
            )
        # The migration role needs its own scoped policy for verification.
        if table == "enterprise":
            op.execute(
                f"""
                CREATE POLICY migration_f1_read ON f1.{table}
                FOR SELECT TO f0d_migration
                USING (id = f1.current_enterprise_id() OR f1.current_enterprise_id() IS NULL)
                """
            )
        else:
            op.execute(
                f"""
                CREATE POLICY migration_f1_read ON f1.{table}
                FOR SELECT TO f0d_migration
                USING (enterprise_id = f1.current_enterprise_id() OR f1.current_enterprise_id() IS NULL)
                """
            )
    # user_profile is an identity table: RLS-scoped to the current sub, plus
    # an unrestricted path for the membership resolver (owner bypass).
    op.execute("ALTER TABLE f1.user_profile ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY user_self ON f1.user_profile
        FOR ALL TO f1_api
        USING (keycloak_sub = f1.current_sub())
        WITH CHECK (keycloak_sub = f1.current_sub())
        """
    )
    # enterprise_user: the resolver reads it (owner bypass); the API has no
    # direct grant.  ENABLE (not FORCE) keeps the SECURITY DEFINER owner read.
    op.execute("ALTER TABLE f1.enterprise_user ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY membership_self ON f1.enterprise_user
        FOR ALL TO f1_api, f1_worker
        USING (enterprise_id = f1.current_enterprise_id())
        WITH CHECK (enterprise_id = f1.current_enterprise_id())
        """
    )


def _grants() -> None:
    op.execute("GRANT USAGE ON SCHEMA f1 TO f1_api, f1_worker")
    # API read/write on tenant tables (audit is append-only: SELECT + INSERT).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON f1.enterprise, f1.plant, f1.document, "
        "f1.upload_task, f1.outbox, f1.qa_request, f1.invite_jti "
        "TO f1_api"
    )
    op.execute("GRANT SELECT, INSERT ON f1.audit_log TO f1_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.user_profile TO f1_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.enterprise_user TO f1_api")
    # Worker read/update on workflow tables + audit insert.
    op.execute(
        "GRANT SELECT, UPDATE ON f1.document, f1.upload_task TO f1_worker"
    )
    op.execute("GRANT SELECT ON f1.enterprise TO f1_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.outbox TO f1_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.qa_request TO f1_worker")
    op.execute("GRANT INSERT ON f1.audit_log TO f1_worker")
    op.execute("GRANT SELECT ON f1.user_profile, f1.enterprise_user TO f1_worker")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.resolve_enterprise_for_sub(text) TO f1_api, f1_worker"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.current_enterprise_id() TO f1_api, f1_worker"
    )
    op.execute("GRANT EXECUTE ON FUNCTION f1.current_sub() TO f1_api, f1_worker")
    op.execute("GRANT EXECUTE ON FUNCTION f1.context_session_authorized(uuid) TO f1_api, f1_worker")
    # Append-only guard must also reject direct UPDATE/DELETE from the owner's
    # own verification path.
    op.execute("REVOKE UPDATE, DELETE ON f1.audit_log FROM PUBLIC, f0d_runtime, f0d_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS f1.invite_jti CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.qa_request CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.outbox CASCADE")
    op.execute("DROP TABLE IF EXISTS f1.upload_task CASCADE")
    op.execute("ALTER TABLE f1.document DROP CONSTRAINT IF EXISTS document_plant_enterprise_fk")
    op.execute("ALTER TABLE f1.plant DROP CONSTRAINT IF EXISTS plant_enterprise_id_uq")
    op.execute("DROP TRIGGER IF EXISTS audit_append_only ON f1.audit_log")
    op.execute("ALTER TABLE f1.enterprise DROP COLUMN IF EXISTS f0i_enterprise_id")
    # Drop RLS policies before dropping the tenant column they reference.
    # (The workflow tables above are CASCADE-dropped, so only the baseline
    # tenant tables survive here.)
    for table in ("enterprise", "plant", "document", "audit_log"):
        op.execute(f"DROP POLICY IF EXISTS tenant_boundary ON f1.{table}")
        op.execute(f"DROP POLICY IF EXISTS migration_f1_read ON f1.{table}")
        op.execute(f"ALTER TABLE f1.{table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.audit_log DROP COLUMN IF EXISTS enterprise_id")
    op.execute("DROP POLICY IF EXISTS user_self ON f1.user_profile")
    op.execute("DROP POLICY IF EXISTS membership_self ON f1.enterprise_user")
    op.execute("ALTER TABLE f1.user_profile DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.enterprise_user DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS f1.resolve_enterprise_for_sub(text)")
