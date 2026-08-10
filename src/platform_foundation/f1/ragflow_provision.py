"""F1 RAGFlow provisioning: embedding provider + per-enterprise datasets.

Idempotent: re-running ensures the VolcEngine provider + ``ark-probe``
instance (doubao-embedding-vision) exist and returns each enterprise's
dataset (creating it on first run).  API keys are read from the f0j1 secrets
dir at call time; never logged.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from typing import Any

from platform_foundation.f0j1.ragflow_client import (
    RagFlowClient,
    RagFlowProbeError,
)

from .config import ragflow_base_url as _ragflow_base
from .config import redis_url as _redis_url
from .secret_files import SecretFileError, read_provider_secret_text

RAGFLOW_BASE = _ragflow_base()
EMBEDDING_MODEL = "doubao-embedding-vision@VolcEngine"
EMBEDDING_MODEL_NAME = "doubao-embedding-vision"
PROVIDER = "VolcEngine"
INSTANCE = "ark-probe"
# Leader-provided Ark plan endpoint + key (2026-08-08); the vector model
# does not support Auto/console switching.
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
class RagflowProvisionError(RuntimeError):
    pass


def ragflow_token() -> str:
    try:
        return read_provider_secret_text(
            "ragflow_api_key", file_env="F1_RAGFLOW_API_KEY_FILE"
        )
    except SecretFileError:
        raise RagflowProvisionError("RAGFLOW_TOKEN_UNAVAILABLE") from None


def ark_api_key() -> str:
    try:
        return read_provider_secret_text(
            "ark_api_key", file_env="F1_ARK_API_KEY_FILE"
        )
    except SecretFileError:
        raise RagflowProvisionError("ARK_KEY_UNAVAILABLE") from None


@contextmanager
def ragflow_lock(identity: str):
    """Cross-process lock for list/create/reconcile operations.

    Only a SHA of the opaque identity becomes a Redis key.  The bounded lease
    prevents a dead process from permanently blocking recovery.
    """
    from redis import Redis
    from redis.exceptions import LockError

    digest = hashlib.sha256(identity.encode("ascii", errors="strict")).hexdigest()
    lock = Redis.from_url(_redis_url()).lock(
        f"f1-ragflow-{digest}", timeout=90, blocking_timeout=30
    )
    acquired = bool(lock.acquire())
    if not acquired:
        raise RagflowProvisionError("RAGFLOW_LOCK_TIMEOUT")
    try:
        yield
    finally:
        try:
            lock.release()
        except LockError:
            # An expired owner must never delete a newer owner's lock.
            pass


def _client() -> tuple[RagFlowClient, str]:
    return RagFlowClient(base_url=RAGFLOW_BASE), ragflow_token()


def _ensure_provider(client: RagFlowClient, token: str) -> None:
    """Idempotently create the VolcEngine provider + ark-probe instance."""
    try:
        client.add_provider(token, PROVIDER)
    except RagFlowProbeError:
        pass  # provider already exists
    api_key = ark_api_key()
    model_info = [
        {
            "model_name": EMBEDDING_MODEL_NAME,
            "model_type": ["embedding"],
        }
    ]
    try:
        client.create_provider_instance(
            token,
            PROVIDER,
            INSTANCE,
            api_key,
            ARK_BASE_URL,
            model_info,
        )
    except RagFlowProbeError as error:
        # instance already exists is acceptable (idempotent).
        if "already" not in str(error).lower() and "409" not in str(error):
            raise


def dataset_for_enterprise(enterprise_id: uuid.UUID) -> str:
    """Return the enterprise's dataset id, creating it if absent."""
    client, token = _client()
    name = f"f1-enterprise-{enterprise_id.hex}"
    try:
        with ragflow_lock(name):
            datasets = client.list_datasets(token)
            matching = [dataset for dataset in datasets if dataset.get("name") == name]
            if len(matching) > 1:
                raise RagflowProvisionError("RAGFLOW_DATASET_BINDING_AMBIGUOUS")
            if matching:
                return matching[0]["id"]
            created = client.create_dataset(token, name, EMBEDDING_MODEL)
            return created["id"]
    except RagFlowProbeError as error:
        raise RagflowProvisionError(f"DATASET_OP_FAILED {error}") from error


__all__ = (
    "dataset_for_enterprise", "ragflow_lock", "RagflowProvisionError",
    "EMBEDDING_MODEL",
)
