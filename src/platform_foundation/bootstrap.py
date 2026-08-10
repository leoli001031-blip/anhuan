"""Owner-only, idempotent seed for local fixture principals and sources."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import CatalogEntry, load_catalog
from .database import DatabaseConfig, role_transaction


_IDENTITY_NAMESPACE = uuid.UUID("8a34768c-85ea-57bb-98d6-b2ed234c48e0")
LOCAL_TENANT_A_TOKEN = "f0d_local_fixture_a_4d6b39c5d7c94d0a9b391243"
LOCAL_TENANT_B_TOKEN = "f0d_local_fixture_b_92a3b7f41e294abcb27f804d"


@dataclass(frozen=True, slots=True)
class LocalPrincipal:
    enterprise_id: uuid.UUID
    actor_id: uuid.UUID
    session_id: uuid.UUID
    token: str

    @property
    def token_sha256(self) -> str:
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()


TENANT_A = LocalPrincipal(
    enterprise_id=uuid.uuid5(_IDENTITY_NAMESPACE, "enterprise-a"),
    actor_id=uuid.uuid5(_IDENTITY_NAMESPACE, "actor-a"),
    session_id=uuid.uuid5(_IDENTITY_NAMESPACE, "session-a"),
    token=LOCAL_TENANT_A_TOKEN,
)
TENANT_B = LocalPrincipal(
    enterprise_id=uuid.uuid5(_IDENTITY_NAMESPACE, "enterprise-b"),
    actor_id=uuid.uuid5(_IDENTITY_NAMESPACE, "actor-b"),
    session_id=uuid.uuid5(_IDENTITY_NAMESPACE, "session-b"),
    token=LOCAL_TENANT_B_TOKEN,
)


class BootstrapError(RuntimeError):
    def __init__(self, code: str = "LOCAL_BOOTSTRAP_FAILED") -> None:
        self.code = code
        super().__init__(code)


def _seed_principal(
    cursor: object,
    principal: LocalPrincipal,
    *,
    label: str,
    data_context: str,
    fixture_set_id: str | None,
    fixture_version: str | None,
) -> None:
    cursor.execute(  # type: ignore[attr-defined]
        "SELECT set_config('f0d.enterprise_id', %s, true)",
        (str(principal.enterprise_id),),
    )
    cursor.execute(  # type: ignore[attr-defined]
        "SELECT id FROM f0d.enterprise WHERE id = %s",
        (principal.enterprise_id,),
    )
    existing = cursor.fetchone()  # type: ignore[attr-defined]
    if existing is None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT f0d.seed_local_fixture_principal("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                principal.enterprise_id,
                label,
                data_context,
                fixture_set_id,
                fixture_version,
                principal.actor_id,
                "FIXTURE_OPERATOR",
                "FIXTURE_OPERATOR",
                principal.session_id,
                principal.token_sha256,
                datetime(2099, 1, 1, tzinfo=timezone.utc),
            ),
        )
    cursor.execute(  # type: ignore[attr-defined]
        "SELECT e.opaque_label, e.data_context, e.fixture_set_id, "
        "e.fixture_version, e.benchmark_tier, e.external_processing_policy, "
        "e.public_display_allowed, e.production_allowed, e.model_training_allowed, "
        "a.actor_kind, a.status AS actor_status, m.role_code, "
        "m.status AS membership_status, s.token_sha256, s.expires_at, s.revoked_at "
        "FROM f0d.enterprise e "
        "JOIN f0d.enterprise_membership m ON m.enterprise_id=e.id "
        "JOIN f0d.actor a ON a.id=m.actor_id "
        "JOIN f0d.local_fixture_session s "
        "ON s.enterprise_id=e.id AND s.actor_id=a.id "
        "WHERE e.id=%s AND a.id=%s AND s.id=%s",
        (principal.enterprise_id, principal.actor_id, principal.session_id),
    )
    record = cursor.fetchone()  # type: ignore[attr-defined]
    if (
        record is None
        or record["opaque_label"] != label
        or record["data_context"] != data_context
        or record["fixture_set_id"] != fixture_set_id
        or record["fixture_version"] != fixture_version
        or record["benchmark_tier"] != "NONE"
        or record["external_processing_policy"] != "DENY"
        or record["public_display_allowed"] is not False
        or record["production_allowed"] is not False
        or record["model_training_allowed"] is not False
        or record["actor_kind"] != "FIXTURE_OPERATOR"
        or record["role_code"] != "FIXTURE_OPERATOR"
        or record["token_sha256"] != principal.token_sha256
        or record["expires_at"] != datetime(2099, 1, 1, tzinfo=timezone.utc)
        or record["revoked_at"] is not None
        or record["membership_status"] != "ACTIVE"
        or record["actor_status"] != "ACTIVE"
    ):
        raise BootstrapError("LOCAL_PRINCIPAL_MISMATCH")


def _seed_source(cursor: object, principal: LocalPrincipal, entry: CatalogEntry) -> None:
    source_id = registry_source_id(principal.enterprise_id, entry.document_id)
    cursor.execute(  # type: ignore[attr-defined]
        "SELECT set_config('f0d.enterprise_id', %s, true)",
        (str(principal.enterprise_id),),
    )
    cursor.execute(  # type: ignore[attr-defined]
        "INSERT INTO f0d.fixture_source_registry("
        "id,enterprise_id,source_document_id,fixture_set_id,fixture_version,"
        "source_group,source_line,expected_sha256,expected_size_bytes,document_type,"
        "corpus_role,enterprise_fact_allowed,current_regulation_allowed,"
        "search_publish_allowed) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false) "
        "ON CONFLICT DO NOTHING",
        (
            source_id,
            principal.enterprise_id,
            entry.document_id,
            entry.fixture_set_id,
            entry.fixture_version,
            entry.group,
            entry.line,
            entry.expected_sha256,
            entry.expected_size,
            entry.document_type,
            entry.corpus_role,
            entry.enterprise_fact_allowed,
        ),
    )
    cursor.execute(  # type: ignore[attr-defined]
        "SELECT id,fixture_set_id,fixture_version,source_group,source_line,"
        "expected_sha256,expected_size_bytes,document_type,corpus_role,"
        "enterprise_fact_allowed,current_regulation_allowed,search_publish_allowed,"
        "benchmark_tier,external_processing_policy "
        "FROM f0d.fixture_source_registry "
        "WHERE enterprise_id=%s AND source_document_id=%s",
        (principal.enterprise_id, entry.document_id),
    )
    record = cursor.fetchone()  # type: ignore[attr-defined]
    if (
        record is None
        or record["id"] != source_id
        or record["fixture_set_id"] != entry.fixture_set_id
        or record["fixture_version"] != entry.fixture_version
        or record["source_group"] != entry.group
        or record["source_line"] != entry.line
        or record["expected_sha256"] != entry.expected_sha256
        or record["expected_size_bytes"] != entry.expected_size
        or record["document_type"] != entry.document_type
        or record["corpus_role"] != entry.corpus_role
        or record["enterprise_fact_allowed"] is not entry.enterprise_fact_allowed
        or record["current_regulation_allowed"] is not False
        or record["search_publish_allowed"] is not False
        or record["benchmark_tier"] != "NONE"
        or record["external_processing_policy"] != "DENY"
    ):
        raise BootstrapError("LOCAL_SOURCE_MISMATCH")


def seed_local_foundation(config: DatabaseConfig) -> dict[str, int]:
    """Seed two isolated canary tenants and the immutable Fixture registry."""

    entries = load_catalog("full")
    try:
        with role_transaction(config, "f0d_migration") as connection:
            with connection.cursor() as cursor:
                _seed_principal(
                    cursor,
                    TENANT_A,
                    label="FIXTURE_TENANT_A",
                    data_context="LOCAL_FIXTURE",
                    fixture_set_id="environment-demo-seed",
                    fixture_version="v0.1",
                )
                _seed_principal(
                    cursor,
                    TENANT_B,
                    label="FIXTURE_TENANT_B",
                    data_context="SYNTHETIC_CANARY",
                    fixture_set_id=None,
                    fixture_version=None,
                )
                for entry in entries:
                    _seed_source(cursor, TENANT_A, entry)
                cursor.execute(
                    "SELECT set_config('f0d.enterprise_id', %s, true)",
                    (str(TENANT_A.enterprise_id),),
                )
                cursor.execute(
                    "SELECT count(*) AS count FROM f0d.fixture_source_registry"
                )
                if int(cursor.fetchone()["count"]) != len(entries):
                    raise BootstrapError("LOCAL_SOURCE_COUNT_MISMATCH")
                cursor.execute(
                    "SELECT set_config('f0d.enterprise_id', %s, true)",
                    (str(TENANT_B.enterprise_id),),
                )
                cursor.execute(
                    "SELECT count(*) AS count FROM f0d.fixture_source_registry"
                )
                if int(cursor.fetchone()["count"]) != 0:
                    raise BootstrapError("SYNTHETIC_CANARY_SOURCE_PRESENT")
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError() from None
    return {"principals": 2, "registered_sources": len(entries)}


def registry_source_id(
    enterprise_id: uuid.UUID, source_document_id: str
) -> uuid.UUID:
    return uuid.uuid5(
        _IDENTITY_NAMESPACE, f"{enterprise_id}:{source_document_id}"
    )


__all__ = (
    "BootstrapError",
    "LOCAL_TENANT_A_TOKEN",
    "LOCAL_TENANT_B_TOKEN",
    "LocalPrincipal",
    "TENANT_A",
    "TENANT_B",
    "registry_source_id",
    "seed_local_foundation",
)
