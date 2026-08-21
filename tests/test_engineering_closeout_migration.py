"""Static closeout contracts for migration atomicity, RLS, and local seed.

This suite deliberately needs neither Docker nor PostgreSQL.  The live
failure/rollback proof remains the responsibility of the engineering verifier;
these tests prevent the orchestration seams from silently losing that proof.
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
F0_ENV = ROOT / "migrations/env.py"
F1_ENV = ROOT / "infra/f1/alembic/env.py"
F1_MIGRATOR = ROOT / "infra/f1/migrate_f1.py"
LOCAL_MIGRATOR = ROOT / "infra/f1/local_migrate.py"
MATERIAL_RAG_MIGRATOR = ROOT / "infra/f1/material-rag/migrate.py"
LOCAL_SEED = ROOT / "infra/f1/local_seed.py"
LOCAL_ROLES = ROOT / "infra/f1/local/00_roles.sql"
LOCAL_COMPOSE = ROOT / "infra/f1/docker-compose.local.yml"

FROZEN_REVISION_SHA256 = {
    "migrations/versions/f0d_0001_foundation.py":
        "c30ce809caa082338e56237395faf4ad37c9750b24f301700587a33d871eae37",
    "migrations/versions/f0d_0002_context_security.py":
        "23a2ce9e39498084d179b1f7b403e46bf444a680f99d22d97d3d8650e0681e8e",
    "migrations/versions/f0d_0003_local_ocr_evidence.py":
        "8d4079f9a63f9be86f9a74864c239dd91892e287bc00602f40161f1269a7b5fd",
    "migrations/versions/f0d_0004_controlled_body_evidence.py":
        "b597f5ee664a6ae49bd2d93f8c943cc683ec12c406af1ac220b01addd84f930b",
    "migrations/versions/f0d_0005_fixture_annotation_workflow.py":
        "dc763580764dc1d63e09d3337190d69cb49a7298cf683140ac2f12dec5dfe6fc",
    "migrations/versions/f0d_0006_canonical_chunks.py":
        "98237da99ede938d95029947fd4b4dc89714f9b808425816514e890317390625",
    "infra/f1/alembic/versions/f1_0001_platform_shell_baseline.py":
        "18af367b01ff9d5cc8fe514aeba8ffc9e486ef1349d984473e4cfe41d49c5edd",
    "infra/f1/alembic/versions/f1_0002_tenant_boundaries_and_workflow.py":
        "710a2a88f76dadb16a890727f179faa6e44a5ddc27819bd3f6d8be8532b8ca3a",
    "infra/f1/alembic/versions/f1_0003_security_boundaries.py":
        "a8058d00719d26132b24671a4c802c4cea820d0b6ca1a3555a44fa58385d2da9",
    "infra/f1/alembic/versions/f1_0004_repair_boundaries.py":
        "b4befabca47939d7522bffbd8ed577717bead8f923e22120ed56ee138028d521",
    "infra/f1/alembic/versions/f1_0005_business_workbench.py":
        "e1f034cf731a08f2c17615a5f4570afe3bee3443734afac73e67c056d811feb6",
    "infra/f1/alembic/versions/f1_0006_controlled_ingestion.py":
        "65e9c5fb02a33ff34d15edd82dacb6292807c1e1889edad9d2fb6c2610b760aa",
    "infra/f1/alembic/versions/f1_0007_business_views_reports_crm.py":
        "626de17adeedbe9a3869965084a8f5cfc005382ef502117123d08c0219110594",
    "infra/f1/alembic/versions/f1_0008_policy_workflow.py":
        "b1899d294af1c5ac3347c16b49888c79869fbc59134dad0bdb9a9571731f9483",
    "infra/f1/alembic/versions/f1_0009_automated_quality.py":
        "6cc4989dbba7c594b3882935ee581caa75324689d229e4a777d0fa58e9bfdb38",
    "infra/f1/alembic/versions/f1_0010_local_rehearsal.py":
        "2877811fce37b5b688ab1fd87fbebeb3c109ca35a533402b1f1373cdedb1f913",
}

P2_P7_TABLES = {
    "service_case",
    "service_assignment",
    "site_visit",
    "finding",
    "corrective_action",
    "finding_review",
    "business_timeline",
    "in_app_notification",
    "document_record",
    "document_version",
    "document_preview_unit",
    "crm_account",
    "crm_contact",
    "crm_follow_up",
    "business_report",
    "business_report_version",
    "business_report_artifact",
    "policy_source",
    "policy_version",
    "policy_review_event",
    "policy_impact_candidate",
    "policy_impact_task",
    "quality_suite",
    "quality_scenario",
    "quality_run",
    "quality_result",
    "quality_disagreement",
    "rehearsal_plan",
    "rehearsal_check",
    "rehearsal_run",
    "rehearsal_check_result",
}

P2_P7_MIGRATION_TABLES = {
    "f1_0005_business_workbench.py": {
        "service_case", "service_assignment", "site_visit", "finding",
        "corrective_action", "finding_review", "business_timeline",
        "in_app_notification",
    },
    "f1_0006_controlled_ingestion.py": {
        "document_record", "document_version", "document_preview_unit",
    },
    "f1_0007_business_views_reports_crm.py": {
        "crm_account", "crm_contact", "crm_follow_up", "business_report",
        "business_report_version", "business_report_artifact",
    },
    "f1_0008_policy_workflow.py": {
        "policy_source", "policy_version", "policy_review_event",
        "policy_impact_candidate", "policy_impact_task",
    },
    "f1_0009_automated_quality.py": {
        "quality_suite", "quality_scenario", "quality_run", "quality_result",
        "quality_disagreement",
    },
    "f1_0010_local_rehearsal.py": {
        "rehearsal_plan", "rehearsal_check", "rehearsal_run",
        "rehearsal_check_result",
    },
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose_service(source: str, name: str) -> str:
    lines = source.splitlines()
    marker = f"  {name}:"
    try:
        start = lines.index(marker)
    except ValueError:
        raise AssertionError(f"missing compose service: {name}") from None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"  [a-z0-9_-]+:", lines[index]):
            end = index
            break
        if lines[index] and not lines[index].startswith(" "):
            end = index
            break
    return "\n".join(lines[start:end])


def _mounted_secret_volume(service: str) -> str:
    matches = re.findall(
        r"^\s+-\s+([a-z0-9_-]+):/run/secrets/f1:ro\s*$",
        service,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise AssertionError("service must mount exactly one explicit secret volume")
    return matches[0]


def _secret_copy_volumes(compose: str, secret_name: str) -> set[str]:
    secret_init = _compose_service(compose, "secret-init")
    directory_to_volume = {
        directory: volume
        for volume, directory in re.findall(
            r"^\s+-\s+([a-z0-9_-]+):(/[a-z0-9_-]+)\s*$",
            secret_init,
            flags=re.MULTILINE,
        )
    }
    destinations: set[str] = set()
    for names, body in re.findall(
        r"for name in\s+(.*?);\s*do\s+(.*?)\s*done;",
        secret_init,
        flags=re.DOTALL,
    ):
        if secret_name not in names.split():
            continue
        for directory in re.findall(
            r'cp\s+"/source/\$\$\{name\}"\s+"(/[a-z0-9_-]+)/\$\$\{name\}"',
            body,
        ):
            if directory not in directory_to_volume:
                raise AssertionError(
                    f"secret copy target is not a mounted private volume: {directory}"
                )
            destinations.add(directory_to_volume[directory])
    if not destinations:
        raise AssertionError(f"missing secret copy destination: {secret_name}")
    return destinations


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(_source(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {path}:{name}")


def _transaction_body(path: Path, function: str) -> str:
    node = _function(path, function)
    functions = {
        child.name: child
        for child in ast.parse(_source(path), filename=str(path)).body
        if isinstance(child, ast.FunctionDef)
    }
    transaction: ast.With | None = None
    for child in ast.walk(node):
        if not isinstance(child, ast.With):
            continue
        rendered = " ".join(ast.unparse(item.context_expr) for item in child.items)
        if ".begin()" in rendered:
            transaction = child
            break
    if transaction is None:
        raise AssertionError(f"missing transaction: {path}:{function}")

    rendered = "\n".join(ast.unparse(statement) for statement in transaction.body)
    # Include local helpers invoked by the transaction so validation may stay
    # readable without being forced inline merely to satisfy this test.
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, helper in functions.items():
            if name in rendered and name not in seen:
                rendered += "\n" + ast.unparse(helper)
                seen.add(name)
                changed = True
    return rendered


class LocalBootstrapContracts(unittest.TestCase):
    def test_role_bootstrap_reads_the_mounted_files_atomically(self) -> None:
        source = _source(LOCAL_ROLES)
        variable = re.search(r"\\getenv\s+([a-z_]+)\s+POSTGRES_DB", source)
        self.assertIsNotNone(variable)
        assert variable is not None
        self.assertIn(f'DATABASE :"{variable.group(1)}"', source)
        for name in (
            "f0d_migration_password",
            "f0d_runtime_password",
            "f0d_worker_password",
        ):
            self.assertIn(f"/run/secrets/f1/{name}", source)
        self.assertLess(source.index("BEGIN;"), source.index("CREATE ROLE"))
        self.assertGreater(source.rindex("COMMIT;"), source.rindex("ALTER ROLE"))

    def test_nonroot_services_receive_separate_private_secret_volumes(self) -> None:
        source = _source(LOCAL_COMPOSE)
        for directory, volume, target in (
            ("/postgres", "postgres_secrets", "/run/secrets/f1"),
            ("/keycloak", "keycloak_secrets", "/run/secrets/f1"),
            ("/minio", "minio_secrets", "/run/secrets/f1"),
        ):
            with self.subTest(volume=volume):
                self.assertIn(f"chmod 0700", source)
                self.assertIn(directory, source)
                self.assertIn(f"{volume}:{target}:ro", source)

    def test_runtime_services_cannot_read_bootstrap_or_migration_dsns(self) -> None:
        source = _source(LOCAL_COMPOSE)
        migrator_volume = _mounted_secret_volume(
            _compose_service(source, "migrator")
        )
        runtime_volumes = {
            name: _mounted_secret_volume(_compose_service(source, name))
            for name in ("api", "worker", "dispatcher")
        }
        self.assertEqual(len(set(runtime_volumes.values())), len(runtime_volumes))
        self.assertNotIn(migrator_volume, runtime_volumes.values())

        for secret_name in (
            "f0d_migration_dsn",
            "f1_bootstrap_dsn",
            "f1_migration_dsn",
        ):
            with self.subTest(secret=secret_name):
                destinations = _secret_copy_volumes(source, secret_name)
                self.assertIn(migrator_volume, destinations)
                self.assertTrue(destinations.isdisjoint(runtime_volumes.values()))


class AlembicOrchestrationContracts(unittest.TestCase):
    def test_all_historical_revision_bytes_remain_frozen(self) -> None:
        self.assertEqual(len(FROZEN_REVISION_SHA256), 16)
        for relative, expected in FROZEN_REVISION_SHA256.items():
            with self.subTest(path=relative):
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    expected,
                )

    def test_f0_env_remains_frozen_and_f1_external_identity_is_exact(self) -> None:
        f0_bytes = F0_ENV.read_bytes()
        self.assertEqual(
            hashlib.sha256(f0_bytes).hexdigest(),
            "d9947fec7a1977230b26ffabb9b60b6f510595c564f83001168ae1c3a3d9d53e",
        )
        self.assertNotIn('config.attributes.get("connection")', f0_bytes.decode())

        source = _source(F1_ENV)
        self.assertIn('config.attributes.get("connection")', source)
        self.assertIn("SELECT current_user, session_user", source)
        self.assertIn('("f0d_migration", "f0d_bootstrap")', source)
        self.assertIn(
            '"F1_EXTERNAL_CONNECTION_IDENTITY_MISMATCH"', source
        )
        self.assertIn('version_table_schema="f1"', source)

    def test_f1_role_schema_upgrade_and_owner_finalize_stay_ordered(self) -> None:
        source = ast.unparse(_function(F1_MIGRATOR, "migrate_with_connection"))
        markers = (
            "_provision_roles",
            "_ensure_f1_version_schema",
            "SET LOCAL ROLE f0d_migration",
            "command.upgrade",
            "RESET ROLE",
            "after_upgrade()",
            "_finalize_definer_owners",
        )
        positions = [source.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        main_transaction = _transaction_body(F1_MIGRATOR, "main")
        self.assertIn("migrate_with_connection(connection)", main_transaction)

    def test_f1_migrate_target_is_closed_default_0014_not_head(self) -> None:
        node = _function(F1_MIGRATOR, "migrate_with_connection")
        kw_defaults = {
            argument.arg: None if default is None else ast.unparse(default)
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
        }
        self.assertEqual(kw_defaults.get("target"), "F1_DEFAULT_MIGRATE_TARGET")
        upgrade_destinations = [
            ast.unparse(call.args[1])
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and ast.unparse(call.func) == "command.upgrade"
            and len(call.args) >= 2
        ]
        self.assertEqual(upgrade_destinations, ["target"])
        source = ast.unparse(node)
        self.assertNotIn('"head"', source)
        self.assertNotIn("'head'", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("sys.argv", source)
        main_transaction = _transaction_body(F1_MIGRATOR, "main")
        self.assertIn("migrate_with_connection(connection)", main_transaction)
        self.assertNotIn("target=", main_transaction)
        local_transaction = _transaction_body(LOCAL_MIGRATOR, "migrate")
        self.assertIn("migrate_with_connection", local_transaction)
        self.assertNotIn("target=", local_transaction)
        self.assertIn('"f1_0014"', _source(LOCAL_MIGRATOR))
        self.assertNotIn('"f1_0015"', _source(LOCAL_MIGRATOR))

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from infra.f1 import migrate_f1

        self.assertEqual(
            migrate_f1.F1_ALLOWED_MIGRATE_TARGETS,
            frozenset({"f1_0014", "f1_0015", "f1_0016"}),
        )
        self.assertEqual(migrate_f1.F1_DEFAULT_MIGRATE_TARGET, "f1_0014")
        self.assertEqual(migrate_f1.F1_MATERIAL_RAG_MIGRATE_TARGET, "f1_0016")
        self.assertEqual(migrate_f1._closed_f1_migrate_target("f1_0014"), "f1_0014")
        self.assertEqual(migrate_f1._closed_f1_migrate_target("f1_0015"), "f1_0015")
        self.assertEqual(migrate_f1._closed_f1_migrate_target("f1_0016"), "f1_0016")
        closed_source = ast.unparse(_function(F1_MIGRATOR, "_closed_f1_migrate_target"))
        self.assertIn("type(target) is not str", closed_source)
        self.assertNotIn("isinstance(target", closed_source)
        self.assertLess(
            source.index("_closed_f1_migrate_target"),
            source.index("driver_connection"),
        )
        self.assertLess(
            source.index("_closed_f1_migrate_target"),
            source.index("command.upgrade"),
        )
        rag_transaction = _transaction_body(MATERIAL_RAG_MIGRATOR, "migrate")
        self.assertIn("migrate_with_connection", rag_transaction)
        self.assertIn("target=migrate_f1.F1_MATERIAL_RAG_MIGRATE_TARGET", rag_transaction)
        rag_migrate = ast.unparse(_function(MATERIAL_RAG_MIGRATOR, "migrate"))
        self.assertNotIn("os.environ", rag_migrate)
        self.assertNotIn("sys.argv", rag_migrate)
        self.assertNotIn("os.environ", _source(LOCAL_MIGRATOR))
        self.assertNotIn("sys.argv", _source(LOCAL_MIGRATOR))
        self.assertNotIn("sys.argv", _source(F1_MIGRATOR))
        self.assertNotIn("F1_MATERIAL_RAG_MIGRATE_TARGET", main_transaction)
        self.assertNotIn("F1_MATERIAL_RAG_MIGRATE_TARGET", local_transaction)
        for illegal in (
            "head",
            "f1_0013",
            "f1_0017",
            "f1_0014 ",
            "",
            None,
            14,
            [],
            {},
            set(),
            bytearray(),
        ):
            with self.subTest(illegal=repr(illegal)):
                with self.assertRaises(RuntimeError) as raised:
                    migrate_f1._closed_f1_migrate_target(illegal)
                self.assertIs(type(raised.exception), RuntimeError)
                self.assertEqual(str(raised.exception), "F1_MIGRATE_TARGET_INVALID")

    def test_combined_migration_checks_heads_and_rls_before_commit(self) -> None:
        transaction = _transaction_body(LOCAL_MIGRATOR, "migrate")
        self.assertIn("_upgrade_f0(connection)", transaction)
        self.assertIn("migrate_with_connection", transaction)
        self.assertIn("LOCAL_MIGRATION_HEAD_MISMATCH", transaction)
        self.assertIn("LOCAL_RLS_CATALOG_MISMATCH", transaction)
        self.assertIn("relrowsecurity", transaction)
        self.assertIn("relforcerowsecurity", transaction)

    def test_all_31_business_tables_are_created_with_force_rls(self) -> None:
        self.assertEqual(len(P2_P7_TABLES), 31)
        self.assertEqual(
            set().union(*P2_P7_MIGRATION_TABLES.values()), P2_P7_TABLES
        )
        for filename, tables in P2_P7_MIGRATION_TABLES.items():
            source = _source(ROOT / "infra/f1/alembic/versions" / filename)
            for table in sorted(tables):
                with self.subTest(migration=filename, table=table):
                    self.assertIn(f"CREATE TABLE f1.{table} (", source)
                    explicit_enable = (
                        f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY"
                    )
                    explicit_force = (
                        f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY"
                    )
                    loop_enable = "ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY"
                    loop_force = "ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY"
                    self.assertTrue(
                        explicit_enable in source
                        or (f'"{table}"' in source and loop_enable in source)
                    )
                    self.assertTrue(
                        explicit_force in source
                        or (f'"{table}"' in source and loop_force in source)
                    )

    def test_runtime_roles_are_fail_closed(self) -> None:
        source = _source(F1_MIGRATOR)
        for marker in (
            "rolcanlogin",
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolinherit",
            "rolreplication",
            "rolbypassrls",
            "rolconnlimit",
            "F1_RUNTIME_ROLE_UNSAFE",
            "F1_ROLE_MEMBERSHIP_FORBIDDEN",
        ):
            self.assertIn(marker, source)


class LocalSeedContracts(unittest.TestCase):
    def test_seed_is_one_transaction_and_covers_every_business_role(self) -> None:
        source = _source(LOCAL_SEED)
        self.assertNotIn("autocommit=True", source)
        self.assertEqual(source.count("connection.commit()"), 1)
        self.assertIn("f0i_enterprise_id) VALUES (%s,%s,%s,NULL)", source)
        self.assertNotIn("F0I_TENANT", source)
        self.assertIn("ON CONFLICT (id) DO NOTHING", source)
        self.assertIn(
            "ON CONFLICT (enterprise_id,user_id) DO NOTHING", source
        )
        for role in (
            "super_admin",
            "enterprise_admin",
            "plant_admin",
            "auditor",
            "partner",
        ):
            self.assertIn(f'"{role}"', source)
        for reason in (
            "LOCAL_SEED_ENTERPRISE_MISMATCH",
            "LOCAL_SEED_PROFILE_MISMATCH",
            "LOCAL_SEED_MEMBERSHIP_MISMATCH",
        ):
            self.assertIn(reason, source)


if __name__ == "__main__":
    unittest.main()
