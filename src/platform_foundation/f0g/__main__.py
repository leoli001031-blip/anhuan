from __future__ import annotations

import argparse
import json
import os

from ..auth import authenticate_local_session
from ..bootstrap import LOCAL_TENANT_A_TOKEN
from ..database import DatabaseConfig
from ..f0f.acceptance import ACCEPTANCE_KEY_FILE
from .api import check_local_server_binding, run_local_api
from .artifacts import generate_artifacts
from .config import validate_local_database_config
from .preparation import prepare_workflow
from .service import AnnotationService
from .tokens import ACCEPTANCE_TOKEN_BUNDLE


def _config() -> DatabaseConfig:
    base = "127.0.0.1:55432/f0g_acceptance_v01"
    defaults = {
        "migration_dsn": "postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        "runtime_dsn": "postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        "worker_dsn": "postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    }
    values = {
        "migration_dsn": os.environ.get("F0G_MIGRATION_DSN", defaults["migration_dsn"]),
        "runtime_dsn": os.environ.get("F0G_RUNTIME_DSN", defaults["runtime_dsn"]),
        "worker_dsn": os.environ.get("F0G_WORKER_DSN", defaults["worker_dsn"]),
    }
    return validate_local_database_config(DatabaseConfig(**values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f0g-fixture-annotation")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    commands.add_parser("artifacts")
    serve = commands.add_parser("serve")
    serve.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = _config()
        if args.command == "prepare":
            operator = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
            result: dict[str, object] = prepare_workflow(
                config, operator, ACCEPTANCE_TOKEN_BUNDLE
            ).to_dict()
        elif args.command == "artifacts":
            operator = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
            result = {
                "schema": "f0g-artifact-result-v1",
                "status": "LOCAL_FIXTURE_ANNOTATION_WORKFLOW_READY",
                **generate_artifacts(config, operator, ACCEPTANCE_TOKEN_BUNDLE),
            }
        elif args.command == "serve":
            service = AnnotationService(config, ACCEPTANCE_KEY_FILE)
            if args.check:
                result = check_local_server_binding(service)
            else:
                run_local_api(service)
                return 0
        else:
            return 2
    except Exception:
        print(
            json.dumps(
                {"status": "FAILED", "reason_code": "F0G_OPERATION_FAILED"},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
