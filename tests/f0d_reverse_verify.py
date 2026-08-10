from __future__ import annotations

import os
import tempfile
from pathlib import Path

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
)
from platform_foundation.database import DatabaseConfig, tenant_transaction
from platform_foundation.governance import (
    GovernanceDenied,
    require_acceptance_gold_promotion,
    require_external_processing,
    require_production_entry,
    require_professional_publication,
    require_real_customer_upload,
    require_uat_entry,
)
from platform_foundation.vault import LocalFixtureVault, VaultError


def _config() -> DatabaseConfig:
    return DatabaseConfig(
        migration_dsn=os.environ.get(
            "F0D_MIGRATION_DSN",
            "postgresql://f0d_migration:f0d-migration-local-v01@127.0.0.1:55432/f0d",
        ),
        runtime_dsn=os.environ.get(
            "F0D_RUNTIME_DSN",
            "postgresql://f0d_runtime:f0d-runtime-local-v01@127.0.0.1:55432/f0d",
        ),
        worker_dsn=os.environ.get(
            "F0D_WORKER_DSN",
            "postgresql://f0d_worker:f0d-worker-local-v01@127.0.0.1:55432/f0d",
        ),
    )


def main() -> int:
    canary = b"SECRET_REVERSE_BODY_CANARY_9137"
    valid_exit = 2
    tampered_exit = 0
    restored_exit = 2
    body_leaks = 0
    with tempfile.TemporaryDirectory(
        prefix="f0d-reverse-", dir="/private/tmp"
    ) as root:
        with LocalFixtureVault(root) as vault:
            stored = vault.store_bytes(canary)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
                valid_exit = 0
            except VaultError as error:
                body_leaks += canary.decode() in str(error)
            final = Path(root) / "final" / stored.object_id
            final.write_bytes(b"X" * stored.size)
            os.chmod(final, 0o600)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
            except VaultError as error:
                tampered_exit = 2
                body_leaks += canary.decode() in str(error)
            final.write_bytes(canary)
            os.chmod(final, 0o600)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
                restored_exit = 0
            except VaultError as error:
                body_leaks += canary.decode() in str(error)

    tenant_leaks = 0
    try:
        config = _config()
        a = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
        b = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
        for context, foreign in ((a, b.enterprise_id), (b, a.enterprise_id)):
            with tenant_transaction(
                config, "f0d_runtime", context
            ) as connection:
                row = connection.execute(
                    "SELECT count(*) AS count FROM f0d.enterprise WHERE id=%s",
                    (foreign,),
                ).fetchone()
                tenant_leaks += int(row["count"] if row else 1)
        for context in (
            type(a)(b.enterprise_id, a.actor_id, a.session_token_sha256),
            type(a)(b.enterprise_id, b.actor_id, a.session_token_sha256),
        ):
            try:
                with tenant_transaction(config, "f0d_runtime", context):
                    tenant_leaks += 1
            except Exception:
                pass
    except Exception:
        tenant_leaks = 1

    gate_bypasses = 0
    for blocked in (
        require_real_customer_upload,
        require_acceptance_gold_promotion,
        require_external_processing,
        require_professional_publication,
        require_uat_entry,
        require_production_entry,
    ):
        try:
            blocked()
            gate_bypasses += 1
        except GovernanceDenied as error:
            body_leaks += canary.decode() in str(error)

    source_root = Path(__file__).resolve().parents[1] / "src/platform_foundation"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_root.glob("*.py")
    )
    external_calls = int(
        any(
            marker in source
            for marker in (
                "import requests",
                "import httpx",
                "import urllib",
                "import socket",
                "import subprocess",
            )
        )
    )
    ocr_calls = int(
        any(
            marker in source
            for marker in (
                "import fitz",
                "tesseract(",
                "ocrmypdf(",
                "pdftoppm(",
                "soffice(",
            )
        )
    )

    print(f"valid_exit={valid_exit}")
    print(f"tampered_exit={tampered_exit}")
    print(f"restored_exit={restored_exit}")
    print(f"tenant_leaks={tenant_leaks}")
    print(f"body_leaks={body_leaks}")
    print(f"external_calls={external_calls}")
    print(f"ocr_calls={ocr_calls}")
    print(f"gate_bypasses={gate_bypasses}")
    expected = (0, 2, 0, 0, 0, 0, 0, 0)
    observed = (
        valid_exit,
        tampered_exit,
        restored_exit,
        tenant_leaks,
        body_leaks,
        external_calls,
        ocr_calls,
        gate_bypasses,
    )
    return 0 if observed == expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
