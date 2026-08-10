"""Body-free live PostgreSQL boundary verifier for an isolated F1.1.1 DB.

The verifier refuses every database outside the random repair namespace.  It
uses the bootstrap identity only for catalog inspection and exact synthetic
fixture setup/cleanup; all attacks run through the real low-privilege login
roles.  No row body, credential, DSN, path, or exception text is printed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
import re
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import psycopg
from psycopg import sql

from infra.f1 import migrate_f1
from platform_foundation.f1.config import pg_database, pg_host, pg_port


METRICS = (
    "head_mismatches",
    "schema_owner_mismatches",
    "unsafe_definer_roles",
    "definer_memberships",
    "definer_owner_mismatches",
    "definer_search_path_violations",
    "public_definer_exec",
    "rls_force_missing",
    "migration_policy_violations",
    "runtime_role_violations",
    "composite_fk_missing",
    "api_direct_write_acceptances",
    "api_set_role_acceptances",
    "api_schema_create_acceptances",
    "nonmember_visible_rows",
    "migration_write_acceptances",
    "pool_context_leaks",
    "scratch_preexisting_rows",
    "enterprise_control_failures",
    "resolver_scope_violations",
    "invite_escalation_acceptances",
    "invite_concurrency_failures",
    "invite_membership_mismatches",
    "invite_audit_mismatches",
    "upload_claim_failures",
    "upload_token_guard_failures",
    "outbox_claim_failures",
    "outbox_token_guard_failures",
    "qa_claim_state_failures",
    "qa_owner_guard_failures",
    "qa_completion_audit_failures",
    "fixture_cleanup_residuals",
    "catalog_query_failures",
)

_FORMAL_SCRATCH_DATABASE = re.compile(r"^f111_repair_[0-9a-f]{32}$")
_STANDALONE_SCRATCH_DATABASE = re.compile(
    r"^anhuan_f111_repair_[a-z0-9]{6,32}$"
)


def _is_repair_scratch_database(database: str) -> bool:
    return bool(
        _FORMAL_SCRATCH_DATABASE.fullmatch(database)
        or _STANDALONE_SCRATCH_DATABASE.fullmatch(database)
    )


def _random_sha256() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


@dataclass(frozen=True)
class _Fixture:
    """Opaque identifiers for one verifier run; no value is ever rendered."""

    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    authority_enterprise: uuid.UUID = field(default_factory=uuid.uuid4)
    unrelated_enterprise: uuid.UUID = field(default_factory=uuid.uuid4)
    created_enterprise: uuid.UUID = field(default_factory=uuid.uuid4)
    unauthorized_enterprise: uuid.UUID = field(default_factory=uuid.uuid4)
    actor_profile: uuid.UUID = field(default_factory=uuid.uuid4)
    actor_membership: uuid.UUID = field(default_factory=uuid.uuid4)
    restricted_profile: uuid.UUID = field(default_factory=uuid.uuid4)
    restricted_membership: uuid.UUID = field(default_factory=uuid.uuid4)
    upload_document: uuid.UUID = field(default_factory=uuid.uuid4)
    upload_task: uuid.UUID = field(default_factory=uuid.uuid4)
    dispatch_document: uuid.UUID = field(default_factory=uuid.uuid4)
    dispatch_task: uuid.UUID = field(default_factory=uuid.uuid4)
    dispatch_outbox: uuid.UUID = field(default_factory=uuid.uuid4)
    qa_request: uuid.UUID = field(default_factory=uuid.uuid4)
    boundary_audit: uuid.UUID = field(default_factory=uuid.uuid4)
    actor_sub_id: uuid.UUID = field(default_factory=uuid.uuid4)
    restricted_sub_id: uuid.UUID = field(default_factory=uuid.uuid4)
    invited_sub_id: uuid.UUID = field(default_factory=uuid.uuid4)
    invite_id: uuid.UUID = field(default_factory=uuid.uuid4)
    escalation_invite_id: uuid.UUID = field(default_factory=uuid.uuid4)
    tamper_invite_ids: tuple[uuid.UUID, ...] = field(
        default_factory=lambda: tuple(uuid.uuid4() for _index in range(5))
    )
    tamper_sub_ids: tuple[uuid.UUID, ...] = field(
        default_factory=lambda: tuple(uuid.uuid4() for _index in range(5))
    )
    existing_invite_id: uuid.UUID = field(default_factory=uuid.uuid4)
    existing_sub_id: uuid.UUID = field(default_factory=uuid.uuid4)
    existing_profile: uuid.UUID = field(default_factory=uuid.uuid4)
    existing_membership: uuid.UUID = field(default_factory=uuid.uuid4)
    upload_sha: str = field(default_factory=_random_sha256)
    dispatch_sha: str = field(default_factory=_random_sha256)
    qa_sha: str = field(default_factory=_random_sha256)
    qa_other_sha: str = field(default_factory=_random_sha256)
    qa_response_sha: str = field(default_factory=_random_sha256)

    @property
    def actor_sub(self) -> str:
        return "f111-" + self.actor_sub_id.hex

    @property
    def restricted_sub(self) -> str:
        return "f111-" + self.restricted_sub_id.hex

    @property
    def invited_sub(self) -> str:
        return "f111-" + self.invited_sub_id.hex

    @property
    def invite_jti(self) -> str:
        return "jti-" + self.invite_id.hex

    @property
    def escalation_jti(self) -> str:
        return "jti-" + self.escalation_invite_id.hex

    @property
    def tamper_jtis(self) -> tuple[str, ...]:
        return tuple("jti-" + value.hex for value in self.tamper_invite_ids)

    @property
    def tamper_subs(self) -> tuple[str, ...]:
        return tuple("f111-" + value.hex for value in self.tamper_sub_ids)

    @property
    def tamper_emails(self) -> tuple[str, ...]:
        return tuple(value.hex + "@fixture.invalid" for value in self.tamper_sub_ids)

    @property
    def existing_jti(self) -> str:
        return "jti-" + self.existing_invite_id.hex

    @property
    def existing_sub(self) -> str:
        return "f111-" + self.existing_sub_id.hex

    @property
    def existing_email(self) -> str:
        return self.existing_sub_id.hex + "@fixture.invalid"

    @property
    def actor_email(self) -> str:
        return self.actor_sub_id.hex + "@fixture.invalid"

    @property
    def restricted_email(self) -> str:
        return self.restricted_sub_id.hex + "@fixture.invalid"

    @property
    def invited_email(self) -> str:
        return self.invited_sub_id.hex + "@fixture.invalid"

    @property
    def all_subs(self) -> tuple[str, ...]:
        return (
            self.actor_sub,
            self.restricted_sub,
            self.invited_sub,
            self.existing_sub,
            *self.tamper_subs,
        )

    @property
    def all_invite_jtis(self) -> tuple[str, ...]:
        return (
            self.invite_jti,
            self.escalation_jti,
            self.existing_jti,
            *self.tamper_jtis,
        )

    @property
    def audit_resource_ids(self) -> tuple[str, ...]:
        return (
            str(self.created_enterprise),
            str(self.unauthorized_enterprise),
            str(self.boundary_audit),
            str(self.qa_request),
            *self.all_invite_jtis,
        )

    @property
    def created_table(self) -> str:
        return "probe_" + self.run_id.hex[:16]


@dataclass(frozen=True)
class _InviteRejectionState:
    ledger_clean_rows: int
    profile_rows: int
    membership_rows: int
    expected_role_rows: int
    consume_audit_rows: int


def _boundary_failure_counts(
    *,
    authorized_bridge_rows: int,
    document_rows: int,
    audit_rows: int,
    bridge_rows: int,
    document_insert_denied: bool,
) -> tuple[int, int]:
    visible_failures = (
        int(authorized_bridge_rows < 1)
        + int(document_rows != 0)
        + int(audit_rows != 0)
        + int(bridge_rows != 0)
    )
    return visible_failures, int(not document_insert_denied)


def _invite_rejection_failure_count(
    *,
    accepted: bool,
    observed: _InviteRejectionState,
    expected_profile_rows: int,
    expected_membership_rows: int,
    expected_role_rows: int,
) -> int:
    expected = _InviteRejectionState(
        ledger_clean_rows=1,
        profile_rows=expected_profile_rows,
        membership_rows=expected_membership_rows,
        expected_role_rows=expected_role_rows,
        consume_audit_rows=0,
    )
    return int(accepted or observed != expected)


def _role_connection(role: str) -> psycopg.Connection:
    if role not in {"f1_api", "f1_worker"}:
        raise RuntimeError("RUNTIME_ROLE_INVALID")
    return psycopg.connect(
        host=pg_host(),
        port=pg_port(),
        dbname=pg_database(),
        user=role,
        password=migrate_f1._read_secret(f"{role}_password"),
    )


def _migration_connection() -> psycopg.Connection:
    return psycopg.connect(migrate_f1._read_secret("f1_migration_dsn"))


def _scalar(connection: psycopg.Connection, statement: str, params: tuple = ()) -> int:
    row = connection.execute(statement, params).fetchone()
    if row is None:
        raise RuntimeError("CATALOG_RESULT_MISSING")
    return int(row[0])


def _denied(connection: psycopg.Connection, statement: str, params: tuple = ()) -> bool:
    try:
        connection.execute(statement, params)
    except psycopg.Error:
        connection.rollback()
        return True
    connection.rollback()
    return False


def _set_api_context(
    connection: psycopg.Connection, enterprise_id: uuid.UUID, sub: str
) -> None:
    connection.execute(
        "SELECT set_config('f1.enterprise_id', %s, true)",
        (str(enterprise_id),),
    )
    connection.execute("SELECT set_config('f1.sub', %s, true)", (sub,))


def _catalog_metrics(connection: psycopg.Connection) -> dict[str, int]:
    roles = list(migrate_f1.DEFINER_ROLES)
    resolved = migrate_f1._resolved_definer_contract(connection)
    metrics = {name: 0 for name in METRICS}
    metrics["head_mismatches"] = int(
        _scalar(
            connection,
            "SELECT count(*) FROM f1.alembic_version "
            "WHERE version_num = 'f1_0004'",
        )
        != 1
    )
    metrics["schema_owner_mismatches"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_namespace AS n "
        "JOIN pg_roles AS r ON r.oid=n.nspowner "
        "WHERE n.nspname='f1' AND r.rolname<>'f0d_migration'",
    )
    metrics["unsafe_definer_roles"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s) AND "
        "(rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR "
        "rolinherit OR rolreplication OR rolbypassrls)",
        (roles,),
    )
    metrics["definer_memberships"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_auth_members AS m "
        "JOIN pg_roles AS a ON a.oid=m.roleid "
        "JOIN pg_roles AS b ON b.oid=m.member "
        "WHERE a.rolname=ANY(%s) OR b.rolname=ANY(%s)",
        (roles, roles),
    )
    metrics["definer_owner_mismatches"] = sum(
        int(resolved[signature][1] != owner)
        for signature, owner in migrate_f1.ALL_DEFINER_OWNERS.items()
    )
    metrics["definer_search_path_violations"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_proc AS p "
        "JOIN pg_namespace AS n ON n.oid=p.pronamespace "
        "WHERE n.nspname='f1' AND p.prosecdef AND "
        "NOT (COALESCE(p.proconfig,ARRAY[]::text[]) @> "
        "ARRAY['search_path=pg_catalog'])",
    )
    metrics["public_definer_exec"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_proc AS p "
        "JOIN pg_namespace AS n ON n.oid=p.pronamespace, "
        "LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
        "WHERE n.nspname='f1' AND p.prosecdef AND acl.grantee=0 "
        "AND acl.privilege_type='EXECUTE'",
    )
    metrics["rls_force_missing"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_class AS c "
        "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
        "WHERE n.nspname='f1' AND c.relkind='r' "
        "AND c.relname<>'alembic_version' "
        "AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)",
    )
    metrics["migration_policy_violations"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_policies WHERE schemaname='f1' AND "
        "((roles @> ARRAY['f0d_migration']::name[] AND "
        "cmd IN ('INSERT','UPDATE','DELETE','ALL')) OR "
        "COALESCE(qual,'') ILIKE '%%current_enterprise_id() IS NULL%%' OR "
        "COALESCE(with_check,'') ILIKE '%%current_enterprise_id() IS NULL%%' OR "
        "lower(COALESCE(qual,'')) IN ('true','(true)') OR "
        "lower(COALESCE(with_check,'')) IN ('true','(true)'))",
    )
    metrics["runtime_role_violations"] = _scalar(
        connection,
        "SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s) AND "
        "(NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR "
        "rolinherit OR rolreplication OR rolbypassrls)",
        (["f1_api", "f1_worker"],),
    )
    expected_fk = (
        "document_plant_enterprise_fk",
        "upload_task_document_enterprise_fk",
        "outbox_task_enterprise_fk",
    )
    present = _scalar(
        connection,
        "SELECT count(*) FROM pg_constraint AS c "
        "JOIN pg_namespace AS n ON n.oid=c.connamespace "
        "WHERE n.nspname='f1' AND c.contype='f' AND c.conname=ANY(%s)",
        (list(expected_fk),),
    )
    metrics["composite_fk_missing"] = len(expected_fk) - present
    return metrics


def _scratch_business_rows(connection: psycopg.Connection) -> int:
    return _scalar(
        connection,
        "SELECT "
        "(SELECT count(*) FROM f1.enterprise) + "
        "(SELECT count(*) FROM f1.plant) + "
        "(SELECT count(*) FROM f1.user_profile) + "
        "(SELECT count(*) FROM f1.enterprise_user) + "
        "(SELECT count(*) FROM f1.document) + "
        "(SELECT count(*) FROM f1.upload_task) + "
        "(SELECT count(*) FROM f1.outbox) + "
        "(SELECT count(*) FROM f1.qa_request) + "
        "(SELECT count(*) FROM f1.invite_jti) + "
        "(SELECT count(*) FROM f1.audit_log)",
    )


def _f0i_probe(connection: psycopg.Connection) -> tuple[uuid.UUID, str]:
    """Return one registered, session-backed F0-I identity without its body."""

    row = connection.execute(
        "SELECT d.enterprise_id,d.source_object_sha256 "
        "FROM f0i.document_scope AS d "
        "WHERE d.terminal_status='CANONICAL_SCOPE_INCLUDED' "
        "AND d.source_object_sha256 ~ '^[0-9a-f]{64}$' "
        "AND EXISTS (SELECT 1 FROM f0d.local_fixture_session AS s "
        " WHERE s.enterprise_id=d.enterprise_id AND s.revoked_at IS NULL "
        " AND s.expires_at>statement_timestamp()) "
        "ORDER BY d.enterprise_id,d.id LIMIT 1"
    ).fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise RuntimeError("F0I_PROBE_MISSING")
    digest = str(row[1])
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError("F0I_PROBE_MISSING")
    return row[0], digest


def _seed_fixture(fixture: _Fixture) -> None:
    opaque = fixture.run_id.hex
    with psycopg.connect(migrate_f1._bootstrap_dsn(), autocommit=False) as bootstrap:
        f0i_enterprise_id, _digest = _f0i_probe(bootstrap)
        bootstrap.execute(
            "INSERT INTO f1.enterprise(id,name,license_no,f0i_enterprise_id) "
            "VALUES (%s,%s,%s,%s),(%s,%s,%s,NULL)",
            (
                fixture.authority_enterprise,
                opaque,
                opaque,
                f0i_enterprise_id,
                fixture.unrelated_enterprise,
                fixture.unrelated_enterprise.hex,
                fixture.unrelated_enterprise.hex,
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.user_profile(id,keycloak_sub,email) "
            "VALUES (%s,%s,%s),(%s,%s,%s),(%s,%s,%s)",
            (
                fixture.actor_profile,
                fixture.actor_sub,
                fixture.actor_email,
                fixture.restricted_profile,
                fixture.restricted_sub,
                fixture.restricted_email,
                fixture.existing_profile,
                fixture.existing_sub,
                fixture.existing_email,
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.enterprise_user(id,enterprise_id,user_id,role) "
            "VALUES (%s,%s,%s,'super_admin'),(%s,%s,%s,'plant_admin'),"
            "(%s,%s,%s,'enterprise_admin')",
            (
                fixture.actor_membership,
                fixture.authority_enterprise,
                fixture.actor_profile,
                fixture.restricted_membership,
                fixture.authority_enterprise,
                fixture.restricted_profile,
                fixture.existing_membership,
                fixture.authority_enterprise,
                fixture.existing_profile,
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.audit_log("
            "id,enterprise_id,user_sub,action,resource_type,resource_id,result"
            ") VALUES (%s,%s,%s,'boundary.probe','probe',%s,'success')",
            (
                fixture.boundary_audit,
                fixture.authority_enterprise,
                fixture.actor_sub,
                str(fixture.boundary_audit),
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.document("
            "id,enterprise_id,object_key,filename,size,content_type,status"
            ") VALUES (%s,%s,%s,%s,0,'application/pdf','pending'),"
            "(%s,%s,%s,%s,0,'application/pdf','pending')",
            (
                fixture.upload_document,
                fixture.authority_enterprise,
                fixture.upload_document.hex,
                fixture.upload_document.hex,
                fixture.dispatch_document,
                fixture.authority_enterprise,
                fixture.dispatch_document.hex,
                fixture.dispatch_document.hex,
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.upload_task("
            "id,enterprise_id,document_id,object_key,content_sha256,status,"
            "object_state,source_size"
            ") VALUES (%s,%s,%s,%s,%s,'pending','ready',0),"
            "(%s,%s,%s,%s,%s,'pending','ready',0)",
            (
                fixture.upload_task,
                fixture.authority_enterprise,
                fixture.upload_document,
                fixture.upload_document.hex,
                fixture.upload_sha,
                fixture.dispatch_task,
                fixture.authority_enterprise,
                fixture.dispatch_document,
                fixture.dispatch_document.hex,
                fixture.dispatch_sha,
            ),
        )
        bootstrap.execute(
            "INSERT INTO f1.outbox("
            "id,enterprise_id,task_id,event_type,state,payload_sha256,rq_job_id"
            ") VALUES (%s,%s,%s,'upload.dispatched','pending',%s,%s)",
            (
                fixture.dispatch_outbox,
                fixture.authority_enterprise,
                fixture.dispatch_task,
                fixture.dispatch_sha,
                "f111-" + fixture.dispatch_task.hex,
            ),
        )
        bootstrap.commit()


def _consume_invite_once(
    fixture: _Fixture, expires_at: object, barrier: threading.Barrier
) -> int:
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.invited_sub)
        barrier.wait(timeout=30)
        try:
            row = api.execute(
                "SELECT * FROM f1.consume_invite(%s,%s,%s,%s,%s,%s)",
                (
                    fixture.invite_jti,
                    fixture.invited_email,
                    "partner",
                    fixture.authority_enterprise,
                    expires_at,
                    fixture.invited_email,
                ),
            ).fetchone()
        except psycopg.Error:
            api.rollback()
            return 0
        api.commit()
        return int(row is not None)


def _create_invite_probe(
    fixture: _Fixture, *, jti: str, email: str, expires_at: object
) -> bool:
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        try:
            row = api.execute(
                "SELECT f1.create_invite_for_current_sub(%s,%s,'partner',%s)",
                (jti, email, expires_at),
            ).fetchone()
        except psycopg.Error:
            api.rollback()
            return False
        api.commit()
        return row is not None and row[0] is True


def _consume_invite_probe(
    fixture: _Fixture,
    *,
    jti: str,
    sub: str,
    claim_email: str,
    claim_role: str,
    claim_enterprise: uuid.UUID,
    claim_expires_at: object,
    oidc_email: str,
) -> bool:
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, sub)
        try:
            row = api.execute(
                "SELECT * FROM f1.consume_invite(%s,%s,%s,%s,%s,%s)",
                (
                    jti,
                    claim_email,
                    claim_role,
                    claim_enterprise,
                    claim_expires_at,
                    oidc_email,
                ),
            ).fetchone()
        except psycopg.Error:
            api.rollback()
            return False
        api.commit()
        return row is not None


def _invite_rejection_state(
    fixture: _Fixture, *, jti: str, sub: str, expected_role: str
) -> _InviteRejectionState:
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        row = bootstrap.execute(
            "SELECT "
            "(SELECT count(*) FROM f1.invite_jti WHERE jti=%s "
            " AND consumed_at IS NULL AND consumed_by_sub IS NULL),"
            "(SELECT count(*) FROM f1.user_profile WHERE keycloak_sub=%s),"
            "(SELECT count(*) FROM f1.enterprise_user AS eu "
            " JOIN f1.user_profile AS up ON up.id=eu.user_id "
            " WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s),"
            "(SELECT count(*) FROM f1.enterprise_user AS eu "
            " JOIN f1.user_profile AS up ON up.id=eu.user_id "
            " WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s AND eu.role=%s),"
            "(SELECT count(*) FROM f1.audit_log "
            " WHERE action='invite.consume' AND resource_id=%s)",
            (
                jti,
                sub,
                fixture.authority_enterprise,
                sub,
                fixture.authority_enterprise,
                sub,
                expected_role,
                jti,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("INVITE_STATE_MISSING")
    return _InviteRejectionState(*(int(value) for value in row))


def _fixture_residuals(
    connection: psycopg.Connection, fixture: _Fixture
) -> int:
    enterprise_ids = [
        fixture.authority_enterprise,
        fixture.unrelated_enterprise,
        fixture.created_enterprise,
        fixture.unauthorized_enterprise,
    ]
    subs = list(fixture.all_subs)
    resource_ids = list(fixture.audit_resource_ids)
    table_name = "f1." + fixture.created_table
    return _scalar(
        connection,
        "SELECT "
        "(SELECT count(*) FROM f1.enterprise WHERE id=ANY(%s)) + "
        "(SELECT count(*) FROM f1.user_profile WHERE keycloak_sub=ANY(%s)) + "
        "(SELECT count(*) FROM f1.enterprise_user WHERE enterprise_id=ANY(%s)) + "
        "(SELECT count(*) FROM f1.document WHERE id=ANY(%s)) + "
        "(SELECT count(*) FROM f1.upload_task WHERE id=ANY(%s)) + "
        "(SELECT count(*) FROM f1.outbox WHERE id=%s) + "
        "(SELECT count(*) FROM f1.qa_request WHERE request_id=%s) + "
        "(SELECT count(*) FROM f1.invite_jti WHERE jti=ANY(%s)) + "
        "(SELECT count(*) FROM f1.audit_log WHERE resource_id=ANY(%s)) + "
        "(SELECT count(*) FROM pg_class AS c JOIN pg_namespace AS n "
        " ON n.oid=c.relnamespace WHERE n.nspname='f1' "
        " AND (n.nspname||'.'||c.relname)=%s)",
        (
            enterprise_ids,
            subs,
            enterprise_ids,
            [fixture.upload_document, fixture.dispatch_document],
            [fixture.upload_task, fixture.dispatch_task],
            fixture.dispatch_outbox,
            fixture.qa_request,
            list(fixture.all_invite_jtis),
            resource_ids,
            table_name,
        ),
    )


def _cleanup_fixture(fixture: _Fixture) -> int:
    enterprise_ids = [
        fixture.authority_enterprise,
        fixture.unrelated_enterprise,
        fixture.created_enterprise,
        fixture.unauthorized_enterprise,
    ]
    subs = list(fixture.all_subs)
    resource_ids = list(fixture.audit_resource_ids)
    with psycopg.connect(migrate_f1._bootstrap_dsn(), autocommit=False) as bootstrap:
        # SET LOCAL guarantees trigger behaviour returns to normal even when
        # exact cleanup rolls back.  It is permitted only in the prefixed,
        # initially-empty scratch database checked by verify().
        bootstrap.execute("SET LOCAL session_replication_role = 'replica'")
        bootstrap.execute(
            "DELETE FROM f1.audit_log WHERE resource_id=ANY(%s)",
            (resource_ids,),
        )
        bootstrap.execute(
            "DELETE FROM f1.outbox WHERE id=%s", (fixture.dispatch_outbox,)
        )
        bootstrap.execute(
            "DELETE FROM f1.upload_task WHERE id=ANY(%s)",
            ([fixture.upload_task, fixture.dispatch_task],),
        )
        bootstrap.execute(
            "DELETE FROM f1.document WHERE id=ANY(%s)",
            ([fixture.upload_document, fixture.dispatch_document],),
        )
        bootstrap.execute(
            "DELETE FROM f1.qa_request WHERE request_id=%s", (fixture.qa_request,)
        )
        bootstrap.execute(
            "DELETE FROM f1.invite_jti WHERE jti=ANY(%s)",
            (list(fixture.all_invite_jtis),),
        )
        bootstrap.execute(
            "DELETE FROM f1.enterprise_user WHERE enterprise_id=ANY(%s) "
            "AND user_id IN (SELECT id FROM f1.user_profile "
            "WHERE keycloak_sub=ANY(%s))",
            (enterprise_ids, subs),
        )
        bootstrap.execute(
            "DELETE FROM f1.user_profile WHERE keycloak_sub=ANY(%s)", (subs,)
        )
        bootstrap.execute(
            "DELETE FROM f1.enterprise WHERE id=ANY(%s)", (enterprise_ids,)
        )
        bootstrap.execute(
            sql.SQL("DROP TABLE IF EXISTS f1.{}").format(
                sql.Identifier(fixture.created_table)
            )
        )
        bootstrap.execute("SET LOCAL session_replication_role = 'origin'")
        bootstrap.commit()
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        return _fixture_residuals(bootstrap, fixture)


def _baseline_runtime_attacks(metrics: dict[str, int], fixture: _Fixture) -> None:
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        _f0i_enterprise_id, registered_sha = _f0i_probe(bootstrap)

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        authorized_bridge_rows = _scalar(
            api,
            "SELECT count(*) FROM f1.fixture_scope_for_sha(%s)",
            (registered_sha,),
        )
        api.rollback()

    nonmember_sub = fixture.tamper_subs[0]
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, nonmember_sub)
        document_rows = _scalar(
            api,
            "SELECT count(*) FROM f1.document WHERE id=%s",
            (fixture.upload_document,),
        )
        audit_rows = _scalar(
            api,
            "SELECT count(*) FROM f1.audit_log WHERE id=%s",
            (fixture.boundary_audit,),
        )
        bridge_rows = _scalar(
            api,
            "SELECT count(*) FROM f1.fixture_scope_for_sha(%s)",
            (registered_sha,),
        )
        api.rollback()

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, nonmember_sub)
        document_insert_denied = _denied(
            api,
            "INSERT INTO f1.document("
            "id,enterprise_id,object_key,filename,size,content_type,status"
            ") VALUES (%s,%s,%s,%s,0,'application/pdf','pending')",
            (
                uuid.uuid4(),
                fixture.authority_enterprise,
                fixture.run_id.hex,
                fixture.run_id.hex,
            ),
        )

    boundary_visible, boundary_write = _boundary_failure_counts(
        authorized_bridge_rows=authorized_bridge_rows,
        document_rows=document_rows,
        audit_rows=audit_rows,
        bridge_rows=bridge_rows,
        document_insert_denied=document_insert_denied,
    )
    metrics["nonmember_visible_rows"] += boundary_visible
    metrics["api_direct_write_acceptances"] += boundary_write

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.unrelated_enterprise, fixture.actor_sub)
        metrics["nonmember_visible_rows"] += _scalar(
            api,
            "SELECT count(*) FROM f1.enterprise WHERE id=%s",
            (fixture.unrelated_enterprise,),
        )
        api.rollback()

    direct_attacks = (
        (
            "INSERT INTO f1.enterprise(id,name,license_no) VALUES (%s,%s,%s)",
            (uuid.uuid4(), fixture.run_id.hex, fixture.run_id.hex),
        ),
        (
            "INSERT INTO f1.enterprise_user(id,enterprise_id,user_id,role) "
            "VALUES (%s,%s,%s,'super_admin')",
            (uuid.uuid4(), fixture.unrelated_enterprise, fixture.actor_profile),
        ),
    )
    for statement, params in direct_attacks:
        with _role_connection("f1_api") as api:
            _set_api_context(api, fixture.unrelated_enterprise, fixture.actor_sub)
            if not _denied(api, statement, params):
                metrics["api_direct_write_acceptances"] += 1

    for role in migrate_f1.DEFINER_ROLES:
        with _role_connection("f1_api") as api:
            if not _denied(
                api,
                sql.SQL("SET ROLE {}").format(sql.Identifier(role)).as_string(api),
            ):
                metrics["api_set_role_acceptances"] += 1

    with _role_connection("f1_api") as api:
        statement = sql.SQL("CREATE TABLE f1.{}(id integer)").format(
            sql.Identifier(fixture.created_table)
        ).as_string(api)
        if not _denied(api, statement):
            metrics["api_schema_create_acceptances"] += 1

    with _migration_connection() as migration:
        if not _denied(
            migration,
            "INSERT INTO f1.enterprise(id,name,license_no) VALUES (%s,%s,%s)",
            (uuid.uuid4(), fixture.run_id.hex, fixture.run_id.hex),
        ):
            metrics["migration_write_acceptances"] += 1
    with _migration_connection() as migration:
        updated = migration.execute(
            "UPDATE f1.enterprise SET updated_at=statement_timestamp() WHERE id=%s",
            (fixture.unrelated_enterprise,),
        ).rowcount
        migration.rollback()
        metrics["migration_write_acceptances"] += int(updated != 0)
    with _migration_connection() as migration:
        deleted = migration.execute(
            "DELETE FROM f1.enterprise WHERE id=%s",
            (fixture.unrelated_enterprise,),
        ).rowcount
        migration.rollback()
        metrics["migration_write_acceptances"] += int(deleted != 0)

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        api.rollback()
        current = api.execute(
            "SELECT current_setting('f1.enterprise_id',true), "
            "current_setting('f1.sub',true)"
        ).fetchone()
        if current is None or any(value not in {None, ""} for value in current):
            metrics["pool_context_leaks"] += 1
        api.rollback()


def _enterprise_and_invite_semantics(
    metrics: dict[str, int], fixture: _Fixture
) -> None:
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        created = api.execute(
            "SELECT f1.create_enterprise_for_current_sub(%s,%s,%s,%s)",
            (
                fixture.created_enterprise,
                fixture.created_enterprise.hex,
                fixture.created_enterprise.hex,
                fixture.actor_email,
            ),
        ).fetchone()
        api.commit()
    if created is None or created[0] != fixture.created_enterprise:
        metrics["enterprise_control_failures"] += 1

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.restricted_sub)
        if not _denied(
            api,
            "SELECT f1.create_enterprise_for_current_sub(%s,%s,%s,%s)",
            (
                fixture.unauthorized_enterprise,
                fixture.unauthorized_enterprise.hex,
                fixture.unauthorized_enterprise.hex,
                fixture.restricted_email,
            ),
        ):
            metrics["enterprise_control_failures"] += 1

    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        enterprise_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.enterprise WHERE id=%s",
            (fixture.created_enterprise,),
        )
        membership_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.enterprise_user AS eu "
            "JOIN f1.user_profile AS up ON up.id=eu.user_id "
            "WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s "
            "AND eu.role='super_admin'",
            (fixture.created_enterprise, fixture.actor_sub),
        )
        audit_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.audit_log WHERE enterprise_id=%s "
            "AND user_sub=%s AND action='enterprise.create' "
            "AND resource_id=%s AND result='success'",
            (
                fixture.created_enterprise,
                fixture.actor_sub,
                str(fixture.created_enterprise),
            ),
        )
        unauthorized_rows = _scalar(
            bootstrap,
            "SELECT (SELECT count(*) FROM f1.enterprise WHERE id=%s) + "
            "(SELECT count(*) FROM f1.enterprise_user WHERE enterprise_id=%s) + "
            "(SELECT count(*) FROM f1.audit_log WHERE resource_id=%s)",
            (
                fixture.unauthorized_enterprise,
                fixture.unauthorized_enterprise,
                str(fixture.unauthorized_enterprise),
            ),
        )
    metrics["enterprise_control_failures"] += int(
        (enterprise_rows, membership_rows, audit_rows) != (1, 1, 1)
    )
    metrics["enterprise_control_failures"] += unauthorized_rows

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        rows = api.execute(
            "SELECT enterprise_id FROM f1.resolve_current_enterprises()"
        ).fetchall()
        api.rollback()
    resolved_ids = [row[0] for row in rows]
    expected_ids = {fixture.authority_enterprise, fixture.created_enterprise}
    metrics["resolver_scope_violations"] += int(
        len(resolved_ids) != 2 or set(resolved_ids) != expected_ids
    )

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.restricted_sub)
        if not _denied(
            api,
            "SELECT f1.create_invite_for_current_sub("
            "%s,%s,'enterprise_admin',statement_timestamp()+interval '30 minutes')",
            (fixture.escalation_jti, fixture.restricted_email),
        ):
            metrics["invite_escalation_acceptances"] += 1
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        escalation_rows = _scalar(
            bootstrap,
            "SELECT (SELECT count(*) FROM f1.invite_jti WHERE jti=%s) + "
            "(SELECT count(*) FROM f1.audit_log WHERE action='invite.create' "
            "AND resource_id=%s)",
            (fixture.escalation_jti, fixture.escalation_jti),
        )
    metrics["invite_escalation_acceptances"] += escalation_rows

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        expires_row = api.execute(
            "SELECT statement_timestamp()+interval '30 minutes'"
        ).fetchone()
        if expires_row is None:
            raise RuntimeError("INVITE_EXPIRY_MISSING")
        expires_at = expires_row[0]

    tamper_cases = (
        {
            "claim_email": fixture.tamper_emails[0],
            "claim_role": "auditor",
            "claim_enterprise": fixture.authority_enterprise,
            "claim_expires_at": expires_at,
            "oidc_email": fixture.tamper_emails[0],
        },
        {
            "claim_email": fixture.actor_email,
            "claim_role": "partner",
            "claim_enterprise": fixture.authority_enterprise,
            "claim_expires_at": expires_at,
            "oidc_email": fixture.tamper_emails[1],
        },
        {
            "claim_email": fixture.tamper_emails[2],
            "claim_role": "partner",
            "claim_enterprise": fixture.unrelated_enterprise,
            "claim_expires_at": expires_at,
            "oidc_email": fixture.tamper_emails[2],
        },
        {
            "claim_email": fixture.tamper_emails[3],
            "claim_role": "partner",
            "claim_enterprise": fixture.authority_enterprise,
            "claim_expires_at": expires_at + timedelta(seconds=1),
            "oidc_email": fixture.tamper_emails[3],
        },
        {
            "claim_email": fixture.tamper_emails[4],
            "claim_role": "partner",
            "claim_enterprise": fixture.authority_enterprise,
            "claim_expires_at": expires_at,
            "oidc_email": fixture.actor_email,
        },
    )
    for jti, sub, email, case in zip(
        fixture.tamper_jtis,
        fixture.tamper_subs,
        fixture.tamper_emails,
        tamper_cases,
        strict=True,
    ):
        created_probe = _create_invite_probe(
            fixture, jti=jti, email=email, expires_at=expires_at
        )
        accepted = _consume_invite_probe(
            fixture,
            jti=jti,
            sub=sub,
            claim_email=case["claim_email"],
            claim_role=case["claim_role"],
            claim_enterprise=case["claim_enterprise"],
            claim_expires_at=case["claim_expires_at"],
            oidc_email=case["oidc_email"],
        )
        observed = _invite_rejection_state(
            fixture, jti=jti, sub=sub, expected_role="partner"
        )
        metrics["invite_escalation_acceptances"] += int(not created_probe)
        metrics["invite_escalation_acceptances"] += (
            _invite_rejection_failure_count(
                accepted=accepted,
                observed=observed,
                expected_profile_rows=0,
                expected_membership_rows=0,
                expected_role_rows=0,
            )
        )

    existing_created = _create_invite_probe(
        fixture,
        jti=fixture.existing_jti,
        email=fixture.existing_email,
        expires_at=expires_at,
    )
    existing_accepted = _consume_invite_probe(
        fixture,
        jti=fixture.existing_jti,
        sub=fixture.existing_sub,
        claim_email=fixture.existing_email,
        claim_role="partner",
        claim_enterprise=fixture.authority_enterprise,
        claim_expires_at=expires_at,
        oidc_email=fixture.existing_email,
    )
    existing_state = _invite_rejection_state(
        fixture,
        jti=fixture.existing_jti,
        sub=fixture.existing_sub,
        expected_role="enterprise_admin",
    )
    metrics["invite_membership_mismatches"] += int(not existing_created)
    metrics["invite_membership_mismatches"] += _invite_rejection_failure_count(
        accepted=existing_accepted,
        observed=existing_state,
        expected_profile_rows=1,
        expected_membership_rows=1,
        expected_role_rows=1,
    )

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        created_invite = api.execute(
            "SELECT f1.create_invite_for_current_sub(%s,%s,'partner',%s)",
            (fixture.invite_jti, fixture.invited_email, expires_at),
        ).fetchone()
        api.commit()
    if created_invite is None or created_invite[0] is not True:
        metrics["invite_concurrency_failures"] += 1

    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_consume_invite_once, fixture, expires_at, barrier)
            for _index in range(2)
        ]
        winners = sum(future.result(timeout=60) for future in futures)
    metrics["invite_concurrency_failures"] += int(winners != 1)

    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        consumed_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.invite_jti WHERE jti=%s "
            "AND consumed_by_sub=%s AND consumed_at IS NOT NULL",
            (fixture.invite_jti, fixture.invited_sub),
        )
        membership_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.enterprise_user AS eu "
            "JOIN f1.user_profile AS up ON up.id=eu.user_id "
            "WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s "
            "AND eu.role='partner'",
            (fixture.authority_enterprise, fixture.invited_sub),
        )
        audit_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.audit_log WHERE enterprise_id=%s "
            "AND user_sub=%s AND action='invite.consume' "
            "AND resource_id=%s AND result='success'",
            (
                fixture.authority_enterprise,
                fixture.invited_sub,
                fixture.invite_jti,
            ),
        )
    metrics["invite_concurrency_failures"] += int(consumed_rows != 1)
    metrics["invite_membership_mismatches"] += abs(membership_rows - 1)
    metrics["invite_audit_mismatches"] += abs(audit_rows - 1)


def _worker_semantics(metrics: dict[str, int], fixture: _Fixture) -> None:
    with _role_connection("f1_worker") as worker:
        first = worker.execute(
            "SELECT * FROM f1.claim_upload_task(%s,%s,300)",
            (fixture.upload_task, fixture.run_id.hex),
        ).fetchone()
        worker.commit()
    if first is None or first[0] != fixture.authority_enterprise:
        metrics["upload_claim_failures"] += 1
        return
    first_token = first[2]
    if first_token is None:
        metrics["upload_claim_failures"] += 1
        return

    with _role_connection("f1_worker") as worker:
        duplicate = worker.execute(
            "SELECT * FROM f1.claim_upload_task(%s,%s,300)",
            (fixture.upload_task, fixture.run_id.hex),
        ).fetchone()
        worker.commit()
    metrics["upload_claim_failures"] += int(duplicate is not None)

    wrong_token = uuid.uuid4()
    with _role_connection("f1_worker") as worker:
        wrong_renew = worker.execute(
            "SELECT f1.renew_upload_lease(%s,%s,300)",
            (fixture.upload_task, wrong_token),
        ).fetchone()
        worker.commit()
    metrics["upload_token_guard_failures"] += int(
        wrong_renew is None or wrong_renew[0] is not False
    )

    with psycopg.connect(migrate_f1._bootstrap_dsn(), autocommit=True) as bootstrap:
        bootstrap.execute(
            "UPDATE f1.upload_task SET lease_until=statement_timestamp()-interval '1 second' "
            "WHERE id=%s AND lease_token=%s",
            (fixture.upload_task, first_token),
        )
    with _role_connection("f1_worker") as worker:
        expired_renew = worker.execute(
            "SELECT f1.renew_upload_lease(%s,%s,300)",
            (fixture.upload_task, first_token),
        ).fetchone()
        worker.commit()
    metrics["upload_token_guard_failures"] += int(
        expired_renew is None or expired_renew[0] is not False
    )

    with _role_connection("f1_worker") as worker:
        worker.execute(
            "SELECT set_config('f1.enterprise_id',%s,true), "
            "set_config('f1.task_id',%s,true), "
            "set_config('f1.lease_token',%s,true)",
            (
                str(fixture.authority_enterprise),
                str(fixture.upload_task),
                str(first_token),
            ),
        )
        visible = _scalar(
            worker,
            "SELECT count(*) FROM f1.upload_task WHERE id=%s",
            (fixture.upload_task,),
        )
        authorized = worker.execute(
            "SELECT f1.session_authorized(%s)",
            (fixture.authority_enterprise,),
        ).fetchone()
        worker.rollback()
    metrics["upload_token_guard_failures"] += int(
        visible != 0 or authorized is None or authorized[0] is not False
    )

    with _role_connection("f1_worker") as worker:
        reclaimed = worker.execute(
            "SELECT * FROM f1.claim_upload_task(%s,%s,300)",
            (fixture.upload_task, fixture.run_id.hex),
        ).fetchone()
        worker.commit()
    if reclaimed is None or reclaimed[2] is None or reclaimed[2] == first_token:
        metrics["upload_claim_failures"] += 1
        return
    with _role_connection("f1_worker") as worker:
        stale_renew = worker.execute(
            "SELECT f1.renew_upload_lease(%s,%s,300)",
            (fixture.upload_task, first_token),
        ).fetchone()
        worker.execute(
            "SELECT set_config('f1.enterprise_id',%s,true), "
            "set_config('f1.task_id',%s,true), "
            "set_config('f1.lease_token',%s,true)",
            (
                str(fixture.authority_enterprise),
                str(fixture.upload_task),
                str(first_token),
            ),
        )
        stale_visible = _scalar(
            worker,
            "SELECT count(*) FROM f1.upload_task WHERE id=%s",
            (fixture.upload_task,),
        )
        worker.rollback()
    metrics["upload_token_guard_failures"] += int(
        stale_renew is None or stale_renew[0] is not False or stale_visible != 0
    )
    with _role_connection("f1_worker") as worker:
        valid_renew = worker.execute(
            "SELECT f1.renew_upload_lease(%s,%s,300)",
            (fixture.upload_task, reclaimed[2]),
        ).fetchone()
        worker.commit()
    metrics["upload_token_guard_failures"] += int(
        valid_renew is None or valid_renew[0] is not True
    )


def _outbox_semantics(metrics: dict[str, int], fixture: _Fixture) -> None:
    with _role_connection("f1_worker") as worker:
        claimed = worker.execute(
            "SELECT * FROM f1.claim_pending_dispatch(100,300)"
        ).fetchall()
        worker.commit()
    own_rows = [row for row in claimed if row[0] == fixture.dispatch_outbox]
    if len(own_rows) != 1 or own_rows[0][4] is None:
        metrics["outbox_claim_failures"] += 1
        return
    first_token = own_rows[0][4]

    with _role_connection("f1_worker") as worker:
        wrong = worker.execute(
            "SELECT f1.complete_dispatch(%s,%s,true)",
            (fixture.dispatch_outbox, uuid.uuid4()),
        ).fetchone()
        worker.commit()
    metrics["outbox_token_guard_failures"] += int(
        wrong is None or wrong[0] is not False
    )

    with psycopg.connect(migrate_f1._bootstrap_dsn(), autocommit=True) as bootstrap:
        bootstrap.execute(
            "UPDATE f1.outbox SET dispatch_lease_until="
            "statement_timestamp()-interval '1 second' WHERE id=%s "
            "AND dispatch_token=%s",
            (fixture.dispatch_outbox, first_token),
        )
    with _role_connection("f1_worker") as worker:
        expired = worker.execute(
            "SELECT f1.complete_dispatch(%s,%s,true)",
            (fixture.dispatch_outbox, first_token),
        ).fetchone()
        worker.commit()
    metrics["outbox_token_guard_failures"] += int(
        expired is None or expired[0] is not False
    )

    with _role_connection("f1_worker") as worker:
        reclaimed = worker.execute(
            "SELECT * FROM f1.claim_pending_dispatch(100,300)"
        ).fetchall()
        worker.commit()
    own_rows = [row for row in reclaimed if row[0] == fixture.dispatch_outbox]
    if (
        len(own_rows) != 1
        or own_rows[0][4] is None
        or own_rows[0][4] == first_token
    ):
        metrics["outbox_claim_failures"] += 1
        return
    second_token = own_rows[0][4]
    with _role_connection("f1_worker") as worker:
        stale = worker.execute(
            "SELECT f1.complete_dispatch(%s,%s,true)",
            (fixture.dispatch_outbox, first_token),
        ).fetchone()
        worker.commit()
    metrics["outbox_token_guard_failures"] += int(
        stale is None or stale[0] is not False
    )
    with _role_connection("f1_worker") as worker:
        completed = worker.execute(
            "SELECT f1.complete_dispatch(%s,%s,true)",
            (fixture.dispatch_outbox, second_token),
        ).fetchone()
        worker.commit()
    metrics["outbox_token_guard_failures"] += int(
        completed is None or completed[0] is not True
    )
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        state_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.outbox WHERE id=%s AND state='dispatched' "
            "AND dispatch_token=%s AND dispatch_lease_until>statement_timestamp()",
            (fixture.dispatch_outbox, second_token),
        )
    metrics["outbox_token_guard_failures"] += int(state_rows != 1)


def _qa_semantics(metrics: dict[str, int], fixture: _Fixture) -> None:
    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        claimed = api.execute(
            "SELECT * FROM f1.claim_qa_request(%s,%s,300)",
            (fixture.qa_request, fixture.qa_sha),
        ).fetchone()
        api.commit()
    if claimed is None or claimed[0] != "CLAIMED" or claimed[1] is None:
        metrics["qa_claim_state_failures"] += 1
        return
    owner_token = claimed[1]

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        same = api.execute(
            "SELECT * FROM f1.claim_qa_request(%s,%s,300)",
            (fixture.qa_request, fixture.qa_sha),
        ).fetchone()
        different = api.execute(
            "SELECT * FROM f1.claim_qa_request(%s,%s,300)",
            (fixture.qa_request, fixture.qa_other_sha),
        ).fetchone()
        api.commit()
    metrics["qa_claim_state_failures"] += int(
        same is None
        or same[0] != "IN_PROGRESS"
        or same[1] is not None
        or different is None
        or different[0] != "CONFLICT"
        or different[1] is not None
    )

    with _role_connection("f1_api") as api:
        _set_api_context(api, fixture.authority_enterprise, fixture.actor_sub)
        wrong = api.execute(
            "SELECT f1.complete_qa_request("
            "%s,%s,%s,'done',%s,%s,NULL)",
            (
                fixture.qa_request,
                uuid.uuid4(),
                fixture.qa_sha,
                fixture.run_id.bytes,
                fixture.qa_response_sha,
            ),
        ).fetchone()
        api.commit()
    metrics["qa_owner_guard_failures"] += int(wrong is None or wrong[0] is not False)

    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        unchanged = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.qa_request WHERE request_id=%s "
            "AND status='accepted' AND owner_token=%s",
            (fixture.qa_request, owner_token),
        )
        wrong_audits = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.audit_log WHERE action='qa.complete' "
            "AND resource_id=%s",
            (str(fixture.qa_request),),
        )
    metrics["qa_owner_guard_failures"] += int(unchanged != 1 or wrong_audits != 0)

    with _role_connection("f1_api") as connection:
        _set_api_context(connection, fixture.authority_enterprise, fixture.actor_sub)
        rolled_back = connection.execute(
            "SELECT f1.complete_qa_request("
            "%s,%s,%s,'done',%s,%s,NULL)",
            (
                fixture.qa_request,
                owner_token,
                fixture.qa_sha,
                fixture.run_id.bytes,
                fixture.qa_response_sha,
            ),
        ).fetchone()
        row_in_transaction = _scalar(
            connection,
            "SELECT count(*) FROM f1.qa_request WHERE request_id=%s "
            "AND status='done' AND owner_token IS NULL",
            (fixture.qa_request,),
        )
        audit_in_transaction = _scalar(
            connection,
            "SELECT count(*) FROM f1.audit_log WHERE action='qa.complete' "
            "AND resource_id=%s AND result='done'",
            (str(fixture.qa_request),),
        )
        connection.rollback()
    metrics["qa_completion_audit_failures"] += int(
        rolled_back is None
        or rolled_back[0] is not True
        or row_in_transaction != 1
        or audit_in_transaction != 1
    )
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        rollback_state = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.qa_request WHERE request_id=%s "
            "AND status='accepted' AND owner_token=%s",
            (fixture.qa_request, owner_token),
        )
        rollback_audit = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.audit_log WHERE action='qa.complete' "
            "AND resource_id=%s",
            (str(fixture.qa_request),),
        )
    metrics["qa_completion_audit_failures"] += int(
        rollback_state != 1 or rollback_audit != 0
    )

    with _role_connection("f1_api") as connection:
        _set_api_context(connection, fixture.authority_enterprise, fixture.actor_sub)
        completed = connection.execute(
            "SELECT f1.complete_qa_request("
            "%s,%s,%s,'done',%s,%s,NULL)",
            (
                fixture.qa_request,
                owner_token,
                fixture.qa_sha,
                fixture.run_id.bytes,
                fixture.qa_response_sha,
            ),
        ).fetchone()
        final_row_in_transaction = _scalar(
            connection,
            "SELECT count(*) FROM f1.qa_request WHERE request_id=%s "
            "AND status='done' AND owner_token IS NULL",
            (fixture.qa_request,),
        )
        final_audit_in_transaction = _scalar(
            connection,
            "SELECT count(*) FROM f1.audit_log WHERE action='qa.complete' "
            "AND resource_id=%s AND result='done'",
            (str(fixture.qa_request),),
        )
        connection.commit()
    metrics["qa_completion_audit_failures"] += int(
        completed is None
        or completed[0] is not True
        or final_row_in_transaction != 1
        or final_audit_in_transaction != 1
    )
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        final_rows = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.qa_request WHERE request_id=%s "
            "AND status='done' AND response_sha256=%s",
            (fixture.qa_request, fixture.qa_response_sha),
        )
        final_audits = _scalar(
            bootstrap,
            "SELECT count(*) FROM f1.audit_log WHERE enterprise_id=%s "
            "AND user_sub=%s AND action='qa.complete' AND resource_id=%s "
            "AND result='done'",
            (
                fixture.authority_enterprise,
                fixture.actor_sub,
                str(fixture.qa_request),
            ),
        )
    metrics["qa_completion_audit_failures"] += int(
        final_rows != 1 or final_audits != 1
    )


def _runtime_semantics(metrics: dict[str, int]) -> None:
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        metrics["scratch_preexisting_rows"] = _scratch_business_rows(bootstrap)
    if metrics["scratch_preexisting_rows"] != 0:
        return

    fixture = _Fixture()
    try:
        _seed_fixture(fixture)
        _baseline_runtime_attacks(metrics, fixture)
        _enterprise_and_invite_semantics(metrics, fixture)
        _worker_semantics(metrics, fixture)
        _outbox_semantics(metrics, fixture)
        _qa_semantics(metrics, fixture)
    finally:
        metrics["fixture_cleanup_residuals"] += _cleanup_fixture(fixture)


def verify() -> dict[str, int]:
    database = pg_database()
    if not _is_repair_scratch_database(database):
        raise RuntimeError("SCRATCH_DATABASE_REQUIRED")
    with psycopg.connect(migrate_f1._bootstrap_dsn()) as bootstrap:
        metrics = _catalog_metrics(bootstrap)
    _runtime_semantics(metrics)
    return metrics


def _render(metrics: dict[str, int]) -> str:
    return " ".join(f"{name}={int(metrics.get(name, 1))}" for name in METRICS)


def main() -> int:
    metrics = {name: 1 for name in METRICS}
    try:
        metrics = verify()
    except Exception:
        metrics["catalog_query_failures"] = 1
    print(_render(metrics))
    return 0 if all(metrics[name] == 0 for name in METRICS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
