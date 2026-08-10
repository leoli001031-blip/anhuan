"""F1 migration runner (independent Alembic + role provisioning).

Steps:
1. Provision the low-privilege ``f1_api`` / ``f1_worker`` roles (idempotent)
   as the ``f0d_bootstrap`` superuser on the shared fixture PostgreSQL.
2. Run the independent F1 Alembic (``infra/f1/alembic.ini``) to ``head``.
   Its env owns the ``f1.alembic_version`` table, so the frozen F0 head
   (``f0d_0006``) is never clobbered.

The API/Worker never hold the migration role or BYPASSRLS.

Usage: set ``F1_SECRETS_DIR`` to a 0700 directory containing four 0600 files
(runtime-role passwords, ``f1_migration_dsn`` and ``f1_bootstrap_dsn``), then
set the explicit F1 host/port/database variables and run this file.  The two
offline DSNs must name that exact target and their exact database identities.
No credential value is accepted on the command line or written to output.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import Connection, URL, make_url  # noqa: E402

from platform_foundation.f1.config import pg_database, pg_host, pg_port  # noqa: E402

ROLES = ("f1_api", "f1_worker")
ROLE_LIMITS = {"f1_api": 20, "f1_worker": 10}
DEFINER_ROLES = (
    "f1_auth_definer",
    "f1_identity_read_definer",
    "f1_enterprise_create_definer",
    "f1_invite_create_definer",
    "f1_invite_consume_definer",
    "f1_upload_definer",
    "f1_outbox_definer",
    "f1_qa_definer",
)
DEFINER_OWNERS = {
    "f1.session_authorized(uuid)": "f1_auth_definer",
    "f1.resolve_current_enterprises()": "f1_identity_read_definer",
    "f1.create_enterprise_for_current_sub(uuid,text,text,text)": "f1_enterprise_create_definer",
    "f1.create_invite_for_current_sub(text,text,text,timestamptz)": "f1_invite_create_definer",
    "f1.consume_invite(text,text,text,uuid,timestamptz,text)": "f1_invite_consume_definer",
    "f1.claim_upload_task(uuid,text,integer)": "f1_upload_definer",
    "f1.renew_upload_lease(uuid,uuid,integer)": "f1_upload_definer",
    "f1.claim_pending_dispatch(integer,integer)": "f1_outbox_definer",
    "f1.complete_dispatch(uuid,uuid,boolean)": "f1_outbox_definer",
    "f1.claim_qa_request(uuid,text,integer)": "f1_qa_definer",
    "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)": "f1_qa_definer",
}
# The three frozen bridge functions remain owned by the migration role.  They
# receive only a current-enterprise SELECT RLS policy in f1_0004 and no write
# policy; including them here makes the complete SECURITY DEFINER set exact.
LEGACY_DEFINER_OWNERS = {
    "f1.fixture_scope_for_sha(text)": "f0d_migration",
    "f1.fixture_chunks(text,bytea,text)": "f0d_migration",
    "f1.verify_citations(uuid[],bytea,text)": "f0d_migration",
}
ALL_DEFINER_OWNERS = {**DEFINER_OWNERS, **LEGACY_DEFINER_OWNERS}


def _read_secret(name: str) -> str:
    raw_dir = os.environ.get("F1_SECRETS_DIR", "").strip()
    if not raw_dir:
        raise RuntimeError("F1_SECRETS_DIR_REQUIRED")
    directory = Path(raw_dir)
    if not directory.is_absolute():
        raise RuntimeError("F1_SECRETS_DIR_INVALID")
    try:
        directory_info = directory.lstat()
        path = directory / name
        info = path.lstat()
    except OSError:
        raise RuntimeError("F1_SECRET_MISSING") from None
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or stat.S_IMODE(directory_info.st_mode) != 0o700
        or directory_info.st_uid != os.geteuid()
    ):
        raise RuntimeError("F1_SECRETS_DIR_INVALID")
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_size < 1
        or info.st_size > 16384
    ):
        raise RuntimeError("F1_SECRET_PERMISSIONS_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(16385)
    except OSError:
        raise RuntimeError("F1_SECRET_READ_FAILED") from None
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise RuntimeError("F1_SECRET_ENCODING_INVALID") from None
    if not value:
        raise RuntimeError("F1_SECRET_MISSING")
    return value


def _validated_url(secret_name: str, expected_user: str, reason: str) -> URL:
    try:
        url = make_url(_read_secret(secret_name))
    except (TypeError, ValueError):
        raise RuntimeError(reason) from None
    if (
        url.drivername != "postgresql"
        or url.username != expected_user
        or not url.password
        or url.host != pg_host()
        or url.port != int(pg_port())
        or url.database != pg_database()
        or bool(url.query)
    ):
        raise RuntimeError(reason)
    return url


def _bootstrap_dsn() -> str:
    url = _validated_url(
        "f1_bootstrap_dsn",
        "f0d_bootstrap",
        "F1_BOOTSTRAP_DSN_IDENTITY_MISMATCH",
    )
    return url.render_as_string(hide_password=False)


def _migration_dsn() -> str:
    url = _validated_url(
        "f1_migration_dsn",
        "f0d_migration",
        "F1_MIGRATION_DSN_IDENTITY_MISMATCH",
    )
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def _provision_roles(
    connection: psycopg.Connection, database_name: str
) -> None:
    passwords = {
        "f1_api": _read_secret("f1_api_password"),
        "f1_worker": _read_secret("f1_worker_password"),
    }
    for role in DEFINER_ROLES:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if exists is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(role))
            )
        unsafe = connection.execute(
            "SELECT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole "
            "OR rolinherit OR rolreplication OR rolbypassrls "
            "FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if unsafe is None or bool(unsafe[0]):
            raise RuntimeError("F1_DEFINER_ROLE_UNSAFE")

    for role, password in passwords.items():
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()
        if exists is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {} "
                    "PASSWORD {}"
                ).format(
                    sql.Identifier(role),
                    sql.Literal(ROLE_LIMITS[role]),
                    sql.Literal(password),
                )
            )
        flags = connection.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolinherit, rolreplication, rolbypassrls, rolconnlimit "
            "FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if flags is None or tuple(flags) != (
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            ROLE_LIMITS[role],
        ):
            raise RuntimeError("F1_RUNTIME_ROLE_UNSAFE")
        can_connect = connection.execute(
            "SELECT has_database_privilege(%s, %s, 'CONNECT')",
            (role, database_name),
        ).fetchone()
        if can_connect is None or not bool(can_connect[0]):
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(role)
                )
            )

    protected_roles = list((*DEFINER_ROLES, *ROLES))
    membership = connection.execute(
        "SELECT 1 FROM pg_auth_members AS m "
        "JOIN pg_roles AS granted ON granted.oid = m.roleid "
        "JOIN pg_roles AS member ON member.oid = m.member "
        "WHERE granted.rolname = ANY(%s) OR member.rolname = ANY(%s) LIMIT 1",
        (protected_roles, protected_roles),
    ).fetchone()
    if membership is not None:
        raise RuntimeError("F1_ROLE_MEMBERSHIP_FORBIDDEN")


def _ensure_f1_version_schema(connection: psycopg.Connection) -> None:
    """Create the independent Alembic version schema exactly once.

    Alembic creates ``f1.alembic_version`` before executing the root revision,
    so a truly fresh database needs the namespace first.  Existing namespaces
    are accepted only when owned by the migration role; replay performs no DDL.
    """
    owner = connection.execute(
        "SELECT r.rolname FROM pg_namespace AS n "
        "JOIN pg_roles AS r ON r.oid = n.nspowner WHERE n.nspname = 'f1'"
    ).fetchone()
    if owner is None:
        connection.execute("CREATE SCHEMA f1 AUTHORIZATION f0d_migration")
        connection.execute("REVOKE ALL ON SCHEMA f1 FROM PUBLIC")
        return
    if str(owner[0]) != "f0d_migration":
        raise RuntimeError("F1_SCHEMA_OWNER_MISMATCH")


def _resolved_definer_contract(connection: psycopg.Connection) -> dict[str, tuple[int, str]]:
    """Resolve the exact expected function set to OIDs and validate hardening."""
    resolved: dict[str, tuple[int, str]] = {}
    for signature, expected_owner in ALL_DEFINER_OWNERS.items():
        row = connection.execute(
            """
            SELECT p.oid, r.rolname, p.prosecdef, p.proconfig,
                   EXISTS (
                     SELECT 1
                       FROM aclexplode(
                         COALESCE(p.proacl, acldefault('f', p.proowner))
                       ) AS acl
                      WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                   ) AS public_execute
              FROM pg_proc AS p
              JOIN pg_roles AS r ON r.oid = p.proowner
             WHERE p.oid = to_regprocedure(%s)
            """,
            (signature,),
        ).fetchone()
        if row is None:
            raise RuntimeError("F1_DEFINER_SIGNATURE_MISSING")
        oid, owner, prosecdef, proconfig, public_execute = row
        if not bool(prosecdef):
            raise RuntimeError("F1_DEFINER_SECURITY_MODE_INVALID")
        if "search_path=pg_catalog" not in set(proconfig or ()):
            raise RuntimeError("F1_DEFINER_SEARCH_PATH_INVALID")
        if bool(public_execute):
            raise RuntimeError("F1_DEFINER_PUBLIC_EXECUTE")
        resolved[signature] = (int(oid), str(owner))

    actual_oids = {
        int(row[0])
        for row in connection.execute(
            """
            SELECT p.oid
              FROM pg_proc AS p
              JOIN pg_namespace AS n ON n.oid = p.pronamespace
             WHERE n.nspname = 'f1' AND p.prosecdef
            """
        ).fetchall()
    }
    if actual_oids != {oid for oid, _owner in resolved.values()}:
        raise RuntimeError("F1_DEFINER_OWNER_MAP_MISMATCH")
    return resolved


def _alter_owner_by_oid(
    connection: psycopg.Connection, oid: int, role: str
) -> None:
    # The server renders the already-resolved OID as a canonical regprocedure;
    # the role is identifier-quoted by PostgreSQL format(), not interpolated.
    statement = connection.execute(
        "SELECT format('ALTER FUNCTION %%s OWNER TO %%I', "
        "%s::oid::regprocedure, %s::text)",
        (oid, role),
    ).fetchone()
    if statement is None:
        raise RuntimeError("F1_DEFINER_SIGNATURE_MISSING")
    connection.execute(sql.SQL(str(statement[0])))


def _assert_owner_map(
    connection: psycopg.Connection, resolved: dict[str, tuple[int, str]]
) -> None:
    for signature, expected_owner in ALL_DEFINER_OWNERS.items():
        oid = resolved[signature][0]
        actual = connection.execute(
            "SELECT r.rolname FROM pg_proc AS p "
            "JOIN pg_roles AS r ON r.oid = p.proowner WHERE p.oid = %s",
            (oid,),
        ).fetchone()
        if actual is None or str(actual[0]) != expected_owner:
            raise RuntimeError("F1_DEFINER_OWNER_MISMATCH")


def _finalize_definer_owners(connection: psycopg.Connection) -> None:
    """Atomically move the exact function set to membership-free owners.

    Every function is first resolved through ``to_regprocedure`` and checked
    for SECURITY DEFINER, pinned search_path, revoked PUBLIC execution, and an
    exact schema-wide owner map.  Any failure rolls back every owner change;
    rerunning after an interrupted successful finalization is idempotent.
    """
    resolved = _resolved_definer_contract(connection)
    for signature, role in DEFINER_OWNERS.items():
        current_owner = resolved[signature][1]
        if current_owner not in {"f0d_migration", role}:
            raise RuntimeError("F1_DEFINER_OWNER_UNEXPECTED")
    for signature, role in DEFINER_OWNERS.items():
        current_owner = resolved[signature][1]
        if current_owner == role:
            continue
        _alter_owner_by_oid(connection, resolved[signature][0], role)
    _assert_owner_map(connection, resolved)


def _restore_definer_owners(database_name: str) -> None:
    """Bootstrap-only prerequisite for an f1_0004 -> f1_0003 downgrade."""
    with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
        resolved = _resolved_definer_contract(connection)
        for signature, expected_owner in DEFINER_OWNERS.items():
            current_owner = resolved[signature][1]
            if current_owner not in {"f0d_migration", expected_owner}:
                raise RuntimeError("F1_DEFINER_OWNER_UNEXPECTED")
        for signature, expected_owner in DEFINER_OWNERS.items():
            if resolved[signature][1] == "f0d_migration":
                continue
            _alter_owner_by_oid(connection, resolved[signature][0], "f0d_migration")
        for signature in DEFINER_OWNERS:
            oid = resolved[signature][0]
            owner = connection.execute(
                "SELECT r.rolname FROM pg_proc AS p "
                "JOIN pg_roles AS r ON r.oid = p.proowner WHERE p.oid = %s",
                (oid,),
            ).fetchone()
            if owner is None or str(owner[0]) != "f0d_migration":
                raise RuntimeError("F1_DEFINER_OWNER_RESTORE_FAILED")
        connection.commit()


def migrate_with_connection(
    connection: Connection,
    *,
    after_upgrade: object | None = None,
) -> None:
    """Run all F1 DDL and owner finalization in the caller's transaction.

    The optional callback is intentionally Python-only and is used by the
    closeout failure-atomicity test.  It is not exposed through argv or an
    environment switch, so production-like invocations cannot enable a
    migration failpoint accidentally.
    """
    raw = connection.connection.driver_connection
    if not isinstance(raw, psycopg.Connection):
        raise RuntimeError("F1_EXTERNAL_CONNECTION_DRIVER_INVALID")
    identity = raw.execute(
        "SELECT current_user, session_user, current_database()"
    ).fetchone()
    if identity is None or tuple(identity) != (
        "f0d_bootstrap",
        "f0d_bootstrap",
        pg_database(),
    ):
        raise RuntimeError("F1_BOOTSTRAP_CONNECTION_IDENTITY_MISMATCH")

    _provision_roles(raw, pg_database())
    _ensure_f1_version_schema(raw)
    connection.exec_driver_sql("SET LOCAL ROLE f0d_migration")
    try:
        from alembic import command
        from alembic.config import Config

        alembic_ini = str(Path(__file__).resolve().parent / "alembic.ini")
        alembic_config = Config(alembic_ini)
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")
    finally:
        connection.exec_driver_sql("RESET ROLE")

    if after_upgrade is not None:
        if not callable(after_upgrade):
            raise RuntimeError("F1_AFTER_UPGRADE_CALLBACK_INVALID")
        after_upgrade()
    _finalize_definer_owners(raw)


def main() -> int:
    pg_database()
    _migration_dsn()  # Validate the independently stored migration identity.
    bootstrap_url = make_url(_bootstrap_dsn()).set(
        drivername="postgresql+psycopg"
    )
    engine = create_engine(bootstrap_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                migrate_with_connection(connection)
    finally:
        engine.dispose()
    print("F1_MIGRATE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
