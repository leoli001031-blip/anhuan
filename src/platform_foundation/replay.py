"""Deterministic smoke/full replay over the registered local fixtures."""

from __future__ import annotations

from .auth import authenticate_local_session
from .bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    registry_source_id,
    seed_local_foundation,
)
from .catalog import load_catalog
from .database import DatabaseConfig
from .service import PlatformService
from .vault import LocalFixtureVault


DEFAULT_VAULT_ROOT = "/private/tmp/anhuan-f0d-v01"


def replay_profile(
    config: DatabaseConfig, profile: str, *, vault_root: str = DEFAULT_VAULT_ROOT
) -> dict[str, object]:
    if profile not in {"smoke", "full"}:
        raise ValueError("INVALID_PROFILE")
    seed_local_foundation(config)
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    full_catalog = load_catalog("full")
    selected = load_catalog(profile)
    with LocalFixtureVault(vault_root) as vault:
        service = PlatformService(config, vault, catalog=full_catalog)
        for entry in selected:
            marker = entry.document_id[:16]
            upload = service.create_upload(
                context,
                registry_source_id(context.enterprise_id, entry.document_id),
                f"replay-create-{marker}",
            )
            service.store_catalog_content(context, upload.upload_id)
            service.complete_upload(
                context, upload.upload_id, f"replay-complete-{marker}"
            )
        relayed = 0
        while service.relay_once(context) is not None:
            relayed += 1
        processed = 0
        while service.process_once(context) is not None:
            processed += 1
        stats = service.stats(context)
        result: dict[str, object] = {
            "schema": "f0d-replay-result-v1",
            "profile": profile,
            "selected_documents": len(selected),
            **stats,
            "relayed_this_run": relayed,
            "processed_this_run": processed,
            "vault_objects": vault.final_count(),
            "external_calls": 0,
            "ocr_calls": 0,
            "gold_promotions": 0,
            "professional_publications": 0,
        }
    return result


__all__ = ("DEFAULT_VAULT_ROOT", "replay_profile")
