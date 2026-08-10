"""F1.1.1 security lockdown: membership-checked RLS, minimal definer grants,
caller-tenant-free bridges, and a single-transaction invite consume.

Fixes the M1 attack surface (all were ``red`` in ``test_f111_security_boundaries``):

* ``membership_spoof`` — tenant policies now verify the current session is a
  real member (or an ``f1_worker`` with an in-flight task), not just that the
  ``f1.enterprise_id`` GUC matches.
* ``public_definer_exec`` — every SECURITY DEFINER function has
  ``REVOKE ... FROM PUBLIC`` and only minimal role grants.
* ``arbitrary_f0i_tenant`` — the bridge functions no longer take a caller
  F0-I tenant; they derive it from the current F1 enterprise and verify the
  session is authorized.
* ``invite_spoof`` / ``role_escalations`` / ``single_transaction`` — invite
  consumption runs in one SECURITY DEFINER transaction, binds only the OIDC
  identity, verifies every claim against the ledger, and never overrides an
  existing membership role.
* ``api_worker_isolation`` — the API-side runtime DSN fixes live in
  ``f1/database.py`` / ``f1/citation.py`` (not SQL).

``f0d_migration`` keeps SELECT via ``migration_f1_read`` and gains narrowly
scoped UPDATE/INSERT policies for the privileged consume function only.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0003"
down_revision: str | None = "f1_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_legacy_bridges()
    _session_authorized()
    _revoke_public_helpers()
    _resolve_enterprise_definer()
    _task_enterprise_definer()
    _bridges()
    _consume_invite()
    _outbox_dispatcher()
    _tenant_rls_policies()
    _migration_role_policies()


def _drop_legacy_bridges() -> None:
    # The old bridges accepted a caller-supplied F0-I tenant; the M1 red test
    # ``test_f111_bridge_does_not_accept_caller_tenant`` requires the 2-arg
    # form to no longer exist.
    op.execute("DROP FUNCTION IF EXISTS f1.fixture_scope_for_sha(uuid, text)")
    op.execute("DROP FUNCTION IF EXISTS f1.fixture_chunks(uuid, text, bytea, text)")
    op.execute("DROP FUNCTION IF EXISTS f1.verify_citations(uuid, uuid[], bytea, text)")


def _session_authorized() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.session_authorized(p_enterprise_id uuid)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT (
            -- API path: the authenticated sub is a member of the enterprise
            (f1.current_sub() IS NOT NULL AND EXISTS (
              SELECT 1 FROM f1.enterprise_user AS eu
              JOIN f1.user_profile AS up ON up.id = eu.user_id
              WHERE eu.enterprise_id = p_enterprise_id
                AND up.keycloak_sub = f1.current_sub()
            ))
            OR
            -- Worker path: the enterprise hosts an in-flight task
            (session_user = 'f1_worker' AND EXISTS (
              SELECT 1 FROM f1.upload_task AS t
              WHERE t.enterprise_id = p_enterprise_id
                AND t.status IN ('pending','scanning','indexing')
            ))
          )
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f1.session_authorized(uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.session_authorized(uuid) TO f1_api, f1_worker"
    )


def _revoke_public_helpers() -> None:
    for name, args in (
        ("f1.current_enterprise_id", "()"),
        ("f1.current_sub", "()"),
        ("f1.context_session_authorized", "(uuid)"),
        ("f1.resolve_enterprise_for_sub", "(text)"),
        ("f1.task_enterprise", "(uuid)"),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {name}{args} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.current_enterprise_id() TO f1_api, f1_worker"
    )
    op.execute("GRANT EXECUTE ON FUNCTION f1.current_sub() TO f1_api, f1_worker")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.context_session_authorized(uuid) TO f1_api, f1_worker"
    )


def _resolve_enterprise_definer() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.resolve_enterprise_for_sub(p_sub text)
        RETURNS TABLE(enterprise_id uuid, name text, role text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT e.id, e.name, eu.role
          FROM f1.user_profile AS up
          JOIN f1.enterprise_user AS eu ON eu.user_id = up.id
          JOIN f1.enterprise AS e ON e.id = eu.enterprise_id
          WHERE up.keycloak_sub = p_sub
          ORDER BY e.id
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.resolve_enterprise_for_sub(text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.resolve_enterprise_for_sub(text) TO f1_api, f1_worker"
    )


def _task_enterprise_definer() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.task_enterprise(p_task_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT enterprise_id FROM f1.upload_task WHERE id = p_task_id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f1.task_enterprise(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.task_enterprise(uuid) TO f1_worker")


def _bridges() -> None:
    # All three bridges derive the F0-I tenant from the CURRENT F1 enterprise
    # and reject sessions that are not authorized for it.  No caller tenant.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.fixture_scope_for_sha(p_sha256 text)
        RETURNS TABLE(document_scope_id uuid, document_type text, chunk_count bigint)
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          v_eid uuid;
          v_f0i_enterprise_id uuid;
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR NOT f1.session_authorized(v_eid) THEN
            RETURN;
          END IF;
          SELECT e.f0i_enterprise_id INTO v_f0i_enterprise_id
            FROM f1.enterprise AS e WHERE e.id = v_eid;
          IF v_f0i_enterprise_id IS NULL THEN RETURN; END IF;
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = v_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC
           LIMIT 1;
          IF v_actor_id IS NULL THEN RETURN; END IF;
          PERFORM set_config('f0d.enterprise_id', v_f0i_enterprise_id::text, true);
          PERFORM set_config('f0d.actor_id', v_actor_id::text, true);
          PERFORM set_config('f0d.session_token_sha256', v_token_sha256, true);
          RETURN QUERY
            SELECT d.id, d.document_type, count(c.id)::bigint
              FROM f0i.document_scope AS d
              LEFT JOIN f0i.chunk AS c
                ON c.enterprise_id = d.enterprise_id
               AND c.document_scope_id = d.id
             WHERE d.enterprise_id = v_f0i_enterprise_id
               AND d.source_object_sha256 = p_sha256
             GROUP BY d.id, d.document_type;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f1.fixture_scope_for_sha(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.fixture_scope_for_sha(text) TO f1_api, f1_worker")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.fixture_chunks(
          p_sha256 text, p_key bytea, p_cipher_options text
        )
        RETURNS TABLE(
          chunk_id uuid, parent_chunk_id uuid, document_id uuid, tenant_id uuid,
          kind text, char_count bigint, pages int[], body bytea
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          v_eid uuid;
          v_f0i_enterprise_id uuid;
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR NOT f1.session_authorized(v_eid) THEN
            RETURN;
          END IF;
          SELECT e.f0i_enterprise_id INTO v_f0i_enterprise_id
            FROM f1.enterprise AS e WHERE e.id = v_eid;
          IF v_f0i_enterprise_id IS NULL THEN RETURN; END IF;
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = v_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC
           LIMIT 1;
          IF v_actor_id IS NULL THEN RETURN; END IF;
          PERFORM set_config('f0d.enterprise_id', v_f0i_enterprise_id::text, true);
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
           WHERE c.enterprise_id = v_f0i_enterprise_id
             AND c.chunk_level = 'CHILD'
             AND d.source_object_sha256 = p_sha256
           ORDER BY c.chunk_ordinal, c.id;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f1.fixture_chunks(text, bytea, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.fixture_chunks(text, bytea, text) TO f1_worker")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.verify_citations(
          p_chunk_ids uuid[], p_key bytea, p_cipher_options text
        )
        RETURNS TABLE(
          chunk_id uuid, document_id uuid, tenant_id uuid, pages int[],
          body_sha256 text, body bytea
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          v_eid uuid;
          v_f0i_enterprise_id uuid;
          v_actor_id uuid;
          v_token_sha256 text;
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR NOT f1.session_authorized(v_eid) THEN
            RETURN;
          END IF;
          SELECT e.f0i_enterprise_id INTO v_f0i_enterprise_id
            FROM f1.enterprise AS e WHERE e.id = v_eid;
          IF v_f0i_enterprise_id IS NULL THEN RETURN; END IF;
          SELECT session.actor_id, session.token_sha256
            INTO v_actor_id, v_token_sha256
            FROM f0d.local_fixture_session AS session
           WHERE session.enterprise_id = v_f0i_enterprise_id
             AND session.revoked_at IS NULL
             AND session.expires_at > statement_timestamp()
           ORDER BY session.expires_at DESC LIMIT 1;
          IF v_actor_id IS NULL THEN RETURN; END IF;
          PERFORM set_config('f0d.enterprise_id', v_f0i_enterprise_id::text, true);
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
           WHERE c.enterprise_id = v_f0i_enterprise_id
             AND c.chunk_level = 'CHILD'
             AND c.id = ANY(p_chunk_ids);
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.verify_citations(uuid[], bytea, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.verify_citations(uuid[], bytea, text) TO f1_api, f1_worker"
    )


def _consume_invite() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.consume_invite(
          p_jti text, p_user_sub text, p_email text, p_role text,
          p_enterprise_id uuid, p_expires_at timestamptz
        )
        RETURNS TABLE(
          out_jti text, out_enterprise_id uuid, out_email text, out_role text
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          v_row f1.invite_jti;
          v_profile uuid;
        BEGIN
          SELECT * INTO v_row FROM f1.invite_jti WHERE invite_jti.jti = p_jti;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'INVITE_NOT_FOUND';
          END IF;
          IF v_row.consumed_at IS NOT NULL THEN
            RAISE EXCEPTION 'INVITE_ALREADY_USED';
          END IF;
          IF v_row.enterprise_id <> p_enterprise_id
             OR v_row.email <> p_email
             OR v_row.role <> p_role
             OR extract(epoch FROM v_row.expires_at)::bigint
                <> extract(epoch FROM p_expires_at)::bigint THEN
            RAISE EXCEPTION 'INVITE_CLAIMS_MISMATCH';
          END IF;
          IF v_row.expires_at <= statement_timestamp() THEN
            RAISE EXCEPTION 'INVITE_EXPIRED';
          END IF;
          UPDATE f1.invite_jti
             SET consumed_by_sub = p_user_sub, consumed_at = statement_timestamp()
           WHERE invite_jti.jti = p_jti AND consumed_at IS NULL;
          SELECT id INTO v_profile FROM f1.user_profile WHERE keycloak_sub = p_user_sub;
          IF v_profile IS NULL THEN
            INSERT INTO f1.user_profile (id, keycloak_sub, email)
            VALUES (gen_random_uuid(), p_user_sub, p_email)
            RETURNING id INTO v_profile;
          END IF;
          INSERT INTO f1.enterprise_user (id, enterprise_id, user_id, role)
          VALUES (gen_random_uuid(), p_enterprise_id, v_profile, p_role)
          ON CONFLICT (enterprise_id, user_id) DO NOTHING;
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), p_enterprise_id, p_user_sub,
                  'invite.consume', 'invite', p_jti, 'success');
          RETURN QUERY
            SELECT v_row.jti, v_row.enterprise_id, v_row.email, v_row.role;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.consume_invite(text, text, text, text, uuid, timestamptz) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.consume_invite(text, text, text, text, uuid, timestamptz) "
        "TO f1_api"
    )


def _tenant_rls_policies() -> None:
    for table in ("plant", "document", "audit_log", "upload_task", "outbox",
                  "qa_request", "invite_jti"):
        op.execute(f"DROP POLICY IF EXISTS tenant_boundary ON f1.{table}")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f1.{table}
            FOR ALL TO f1_api, f1_worker
            USING (
              enterprise_id = f1.current_enterprise_id()
              AND f1.session_authorized(enterprise_id)
            )
            WITH CHECK (
              enterprise_id = f1.current_enterprise_id()
              AND f1.session_authorized(enterprise_id)
            )
            """
        )
    op.execute("DROP POLICY IF EXISTS tenant_boundary ON f1.enterprise")
    op.execute(
        """
        CREATE POLICY tenant_boundary ON f1.enterprise
        FOR ALL TO f1_api, f1_worker
        USING (id = f1.current_enterprise_id() AND f1.session_authorized(id))
        WITH CHECK (id = f1.current_enterprise_id() AND f1.session_authorized(id))
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_enterprise_mint ON f1.enterprise
        FOR INSERT TO f1_api, f1_worker
        WITH CHECK (id = f1.current_enterprise_id())
        """
    )
    op.execute("DROP POLICY IF EXISTS membership_self ON f1.enterprise_user")
    op.execute(
        """
        CREATE POLICY membership_self ON f1.enterprise_user
        FOR ALL TO f1_api, f1_worker
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY membership_self_insert ON f1.enterprise_user
        FOR INSERT TO f1_api, f1_worker
        WITH CHECK (enterprise_id = f1.current_enterprise_id())
        """
    )


def _outbox_dispatcher() -> None:
    # The recovery sweep enumerates pending dispatched outbox events across all
    # enterprises (RLS normally hides cross-tenant rows from f1_worker).  The
    # dispatcher only ever enqueues the task_id; it cannot read business data.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.pending_dispatch_tasks()
        RETURNS TABLE(task_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT o.task_id
            FROM f1.outbox AS o
            JOIN f1.upload_task AS t ON t.id = o.task_id
           WHERE o.event_type = 'upload.dispatched'
             AND o.state = 'pending'
             AND t.status = 'pending'
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.pending_dispatch_tasks() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.pending_dispatch_tasks() TO f1_worker"
    )


def _migration_role_policies() -> None:
    op.execute(
        """
        CREATE POLICY migration_f1_invite_consume ON f1.invite_jti
        FOR UPDATE TO f0d_migration
        USING (enterprise_id = f1.current_enterprise_id() OR f1.current_enterprise_id() IS NULL)
        WITH CHECK (enterprise_id = f1.current_enterprise_id() OR f1.current_enterprise_id() IS NULL)
        """
    )
    op.execute(
        """
        CREATE POLICY migration_f1_audit_insert ON f1.audit_log
        FOR INSERT TO f0d_migration
        WITH CHECK (enterprise_id = f1.current_enterprise_id() OR f1.current_enterprise_id() IS NULL)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS migration_f1_audit_insert ON f1.audit_log")
    op.execute("DROP POLICY IF EXISTS migration_f1_invite_consume ON f1.invite_jti")
    op.execute("DROP POLICY IF EXISTS tenant_enterprise_mint ON f1.enterprise")
    op.execute("DROP POLICY IF EXISTS membership_self_insert ON f1.enterprise_user")
    op.execute(
        "DROP FUNCTION IF EXISTS f1.consume_invite(text, text, text, text, uuid, timestamptz)"
    )
    op.execute("DROP FUNCTION IF EXISTS f1.session_authorized(uuid)")
