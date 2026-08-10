"""Transactional F0-F configuration, job, body and annotation service."""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from psycopg.types.json import Jsonb

from ..auth import SessionContext
from ..database import DatabaseConfig, tenant_transaction
from ..service import JobLease
from ..vault import _is_opaque_name
from ..f0e.contracts import OcrPageEvidence, PageRoute
from ..f0e.hashing import stable_uuid4
from ..f0e.routing import build_page_routes, processing_unit_from_row
from .contracts import BoundPageBody, CanonicalBody, F0FError, OcrBodyResult
from .keyfile import LocalFixtureKey
from .runtime_config import RuntimeBundle
from .selection import select_annotation_candidates


_JOB_KIND = "CAPTURE_CONTROLLED_BODY"
_SAFE_WORKER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class BodyConfigurationRecord:
    configuration_id: uuid.UUID
    configuration_sha256: str
    runner_image_id: str
    runner_lock_sha256: str
    runner_profile_sha256: str
    base_f0e_image_id: str
    base_f0e_execution_profile_sha256: str
    runner_protocol: str
    max_plaintext_bytes: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.configuration_id, uuid.UUID)
            or re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.runner_image_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", self.runner_lock_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", self.runner_profile_sha256) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.base_f0e_image_id) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", self.base_f0e_execution_profile_sha256
            )
            is None
            or self.runner_protocol != "f0f-body-result-v1"
            or self.max_plaintext_bytes != 4 * 1024 * 1024
            or self.timeout_seconds != 120
        ):
            raise F0FError("BODY_CONFIGURATION_INVALID")


@dataclass(frozen=True, slots=True)
class BodyPageSource:
    page_evidence_id: uuid.UUID
    evidence_chain_sha256: str
    route: PageRoute
    expected_evidence: OcrPageEvidence


@dataclass(frozen=True, slots=True)
class ControlledBodyExecution:
    lease: JobLease
    processing_plan_id: uuid.UUID
    document_version_id: uuid.UUID
    source_document_id: str
    source_plan_sha256: str
    local_ocr_run_id: uuid.UUID
    local_ocr_output_manifest_sha256: str
    object_blob_id: uuid.UUID
    vault_object_id: str
    input_object_sha256: str
    input_object_size: int
    configuration: BodyConfigurationRecord
    pages: tuple[BodyPageSource, ...]

    @property
    def native_pages(self) -> tuple[BodyPageSource, ...]:
        return tuple(page for page in self.pages if page.route.evidence_method == "NATIVE_REFERENCE")

    @property
    def ocr_pages(self) -> tuple[BodyPageSource, ...]:
        return tuple(page for page in self.pages if page.route.evidence_method == "LOCAL_OCR")


class ControlledBodyService:
    def __init__(self, config: DatabaseConfig) -> None:
        if not isinstance(config, DatabaseConfig):
            raise F0FError("BODY_CONTRACT_INVALID")
        self.config = config

    def register_configuration(
        self,
        context: SessionContext,
        runtime: RuntimeBundle,
        key: LocalFixtureKey,
    ) -> BodyConfigurationRecord:
        if not isinstance(runtime, RuntimeBundle) or not isinstance(key, LocalFixtureKey):
            raise F0FError("BODY_CONFIGURATION_INVALID")
        configuration_id = stable_uuid4(
            "f0f-body-configuration",
            context.enterprise_id,
            runtime.lock_sha256,
            key.fingerprint_sha256,
        )
        verifier_id = stable_uuid4("f0f-key-verifier", configuration_id)
        key_material = bytearray(key.view())
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                row = connection.execute(
                    "SELECT f0f.register_body_configuration("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s) AS configuration_sha256",
                    (
                        configuration_id,
                        verifier_id,
                        runtime.container_image_id,
                        runtime.lock_sha256,
                        runtime.execution_profile_sha256,
                        runtime.base_container_image_id,
                        runtime.base_execution_profile_sha256,
                        "f0f-body-result-v1",
                        key_material,
                    ),
                ).fetchone()
                if row is None:
                    raise F0FError("BODY_CONFIGURATION_INVALID")
                record = connection.execute(
                    "SELECT * FROM f0f.body_configuration WHERE enterprise_id=%s AND id=%s",
                    (context.enterprise_id, configuration_id),
                ).fetchone()
            if record is None or str(row["configuration_sha256"]) != str(
                record["configuration_sha256"]
            ):
                raise F0FError("BODY_CONFIGURATION_INVALID")
            result = _configuration(record)
            if (
                result.runner_image_id != runtime.container_image_id
                or result.runner_lock_sha256 != runtime.lock_sha256
                or result.runner_profile_sha256 != runtime.execution_profile_sha256
                or result.base_f0e_image_id != runtime.base_container_image_id
                or result.base_f0e_execution_profile_sha256
                != runtime.base_execution_profile_sha256
                or result.max_plaintext_bytes != runtime.maximum_body_bytes
                or result.timeout_seconds != runtime.timeout_seconds
            ):
                raise F0FError("BODY_CONFIGURATION_INVALID")
            return result
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None
        finally:
            key_material[:] = b"\0" * len(key_material)
            key_material.clear()

    def enqueue_all(
        self,
        context: SessionContext,
        configuration: BodyConfigurationRecord,
    ) -> tuple[uuid.UUID, ...]:
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                runs = connection.execute(
                    "SELECT id FROM f0e.local_ocr_run WHERE enterprise_id=%s "
                    "AND terminal_status='CANDIDATE_EVIDENCE_RECORDED' "
                    "ORDER BY source_document_id",
                    (context.enterprise_id,),
                ).fetchall()
            return tuple(
                self.enqueue(context, row["id"], configuration) for row in runs
            )
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

    def enqueue(
        self,
        context: SessionContext,
        local_ocr_run_id: uuid.UUID,
        configuration: BodyConfigurationRecord,
    ) -> uuid.UUID:
        if not isinstance(local_ocr_run_id, uuid.UUID) or not isinstance(
            configuration, BodyConfigurationRecord
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT r.*,p.visual_unit_count,p.raw_text_persisted AS plan_raw_text_persisted,"
                        "p.ocr_executed AS plan_ocr_executed,c.configuration_sha256 "
                        "AS body_configuration_sha256 FROM f0e.local_ocr_run r "
                        "JOIN f0d.document_processing_plan p ON p.enterprise_id=r.enterprise_id "
                        "AND p.id=r.processing_plan_id AND p.document_version_id=r.document_version_id "
                        "AND p.source_plan_sha256=r.source_plan_sha256 "
                        "JOIN f0f.body_configuration c ON c.enterprise_id=r.enterprise_id AND c.id=%s "
                        "WHERE r.enterprise_id=%s AND r.id=%s",
                        (
                            configuration.configuration_id,
                            context.enterprise_id,
                            local_ocr_run_id,
                        ),
                    )
                    run = cursor.fetchone()
                    if run is None or not _eligible_run(run, configuration):
                        raise F0FError("BODY_EVIDENCE_MISMATCH")
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (f"F0F:{context.enterprise_id}:{run['processing_plan_id']}",),
                    )
                    cursor.execute(
                        "SELECT id,local_ocr_run_id,local_ocr_output_manifest_sha256,"
                        "controlled_body_configuration_id,controlled_body_configuration_sha256,"
                        "input_version,progress_total FROM f0d.job WHERE enterprise_id=%s "
                        "AND kind=%s AND processing_plan_id=%s",
                        (context.enterprise_id, _JOB_KIND, run["processing_plan_id"]),
                    )
                    existing = cursor.fetchone()
                    input_version = ":".join(
                        (
                            str(run["source_plan_sha256"]),
                            str(run["local_ocr_configuration_sha256"]),
                            str(run["output_manifest_sha256"]),
                            configuration.configuration_sha256,
                        )
                    )
                    if existing is not None:
                        if (
                            existing["local_ocr_run_id"] != local_ocr_run_id
                            or str(existing["local_ocr_output_manifest_sha256"])
                            != str(run["output_manifest_sha256"])
                            or existing["controlled_body_configuration_id"]
                            != configuration.configuration_id
                            or str(existing["controlled_body_configuration_sha256"])
                            != configuration.configuration_sha256
                            or existing["input_version"] != input_version
                            or int(existing["progress_total"])
                            != int(run["visual_unit_count"])
                        ):
                            raise F0FError("BODY_EVIDENCE_MISMATCH")
                        return existing["id"]
                    job_id = stable_uuid4(
                        "f0f-body-job",
                        context.enterprise_id,
                        run["processing_plan_id"],
                        local_ocr_run_id,
                        configuration.configuration_sha256,
                    )
                    trace_id = stable_uuid4("f0f-body-trace", job_id)
                    cursor.execute(
                        "SELECT f0f.controlled_body_job_idempotency_key("
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s) AS key",
                        (
                            context.enterprise_id,
                            run["processing_plan_id"],
                            str(run["source_plan_sha256"]),
                            run["local_ocr_configuration_id"],
                            str(run["local_ocr_configuration_sha256"]),
                            local_ocr_run_id,
                            str(run["output_manifest_sha256"]),
                            configuration.configuration_id,
                            configuration.configuration_sha256,
                        ),
                    )
                    key_row = cursor.fetchone()
                    if key_row is None:
                        raise F0FError("BODY_EVIDENCE_MISMATCH")
                    cursor.execute(
                        "INSERT INTO f0d.job(id,enterprise_id,kind,document_version_id,"
                        "queue_class,priority,idempotency_key,input_version,progress_total,trace_id,"
                        "processing_plan_id,source_plan_sha256,local_ocr_configuration_id,"
                        "local_ocr_configuration_sha256,controlled_body_configuration_id,"
                        "controlled_body_configuration_sha256,local_ocr_run_id,"
                        "local_ocr_output_manifest_sha256) VALUES ("
                        "%s,%s,%s,%s,'document',100,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            job_id,
                            context.enterprise_id,
                            _JOB_KIND,
                            run["document_version_id"],
                            key_row["key"],
                            input_version,
                            run["visual_unit_count"],
                            trace_id,
                            run["processing_plan_id"],
                            run["source_plan_sha256"],
                            run["local_ocr_configuration_id"],
                            run["local_ocr_configuration_sha256"],
                            configuration.configuration_id,
                            configuration.configuration_sha256,
                            local_ocr_run_id,
                            run["output_manifest_sha256"],
                        ),
                    )
                    return job_id
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

    def claim(self, context: SessionContext, worker_id: str) -> JobLease | None:
        if not isinstance(worker_id, str) or _SAFE_WORKER.fullmatch(worker_id) is None:
            raise F0FError("BODY_CONTRACT_INVALID")
        token = uuid.uuid4()
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                row = connection.execute(
                    "SELECT * FROM f0f.claim_controlled_body_job(%s,%s)",
                    (worker_id, token),
                ).fetchone()
            if row is None:
                return None
            return JobLease(
                job_id=row["job_id"],
                generation=int(row["lease_generation"]),
                token=row["lease_token"],
                worker_id=worker_id,
            )
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

    def heartbeat(
        self, context: SessionContext, lease: JobLease, done: int, total: int
    ) -> None:
        _validate_lease(lease)
        if (
            isinstance(done, bool)
            or not isinstance(done, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or not 0 <= done <= total
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                cursor = connection.execute(
                    "UPDATE f0d.job j SET heartbeat_at=statement_timestamp(),"
                    "lease_until=statement_timestamp()+(c.timeout_seconds+30)*interval '1 second',"
                    "progress_done=%s FROM f0f.body_configuration c "
                    "WHERE j.enterprise_id=%s AND j.id=%s AND j.kind=%s "
                    "AND j.status='RUNNING' AND j.lease_owner=%s "
                    "AND j.lease_generation=%s AND j.lease_token=%s "
                    "AND j.lease_until>statement_timestamp() AND j.progress_total=%s "
                    "AND c.enterprise_id=j.enterprise_id "
                    "AND c.id=j.controlled_body_configuration_id "
                    "AND c.configuration_sha256=j.controlled_body_configuration_sha256",
                    (
                        done,
                        context.enterprise_id,
                        lease.job_id,
                        _JOB_KIND,
                        lease.worker_id,
                        lease.generation,
                        lease.token,
                        total,
                    ),
                )
                if cursor.rowcount != 1:
                    raise F0FError("JOB_LEASE_STALE")
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

    def load_execution(
        self, context: SessionContext, lease: JobLease
    ) -> ControlledBodyExecution:
        _validate_lease(lease)
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                chain = connection.execute(
                    "SELECT j.*,j.lease_until>statement_timestamp() AS lease_valid,"
                    "r.source_document_id,r.object_blob_id,r.input_object_sha256,"
                    "r.output_manifest_sha256,r.terminal_status AS run_terminal_status,"
                    "p.visual_unit_count,p.page_count,v.object_blob_id AS version_blob_id,"
                    "b.object_key,b.size_bytes,c.*,oc.execution_profile_sha256 "
                    "AS base_execution_profile_sha256 FROM f0d.job j "
                    "JOIN f0e.local_ocr_run r ON r.enterprise_id=j.enterprise_id "
                    "AND r.id=j.local_ocr_run_id AND r.processing_plan_id=j.processing_plan_id "
                    "AND r.document_version_id=j.document_version_id "
                    "AND r.source_plan_sha256=j.source_plan_sha256 "
                    "AND r.output_manifest_sha256=j.local_ocr_output_manifest_sha256 "
                    "JOIN f0d.document_processing_plan p ON p.enterprise_id=j.enterprise_id "
                    "AND p.id=j.processing_plan_id AND p.document_version_id=j.document_version_id "
                    "AND p.source_plan_sha256=j.source_plan_sha256 "
                    "JOIN f0d.document_version v ON v.enterprise_id=j.enterprise_id "
                    "AND v.id=j.document_version_id AND v.object_blob_id=r.object_blob_id "
                    "JOIN f0d.object_blob b ON b.enterprise_id=j.enterprise_id "
                    "AND b.id=v.object_blob_id AND b.sha256=r.input_object_sha256 "
                    "JOIN f0f.body_configuration c ON c.enterprise_id=j.enterprise_id "
                    "AND c.id=j.controlled_body_configuration_id "
                    "AND c.configuration_sha256=j.controlled_body_configuration_sha256 "
                    "JOIN f0e.local_ocr_configuration oc ON oc.enterprise_id=j.enterprise_id "
                    "AND oc.id=j.local_ocr_configuration_id "
                    "AND oc.configuration_sha256=j.local_ocr_configuration_sha256 "
                    "WHERE j.enterprise_id=%s AND j.id=%s AND j.kind=%s",
                    (context.enterprise_id, lease.job_id, _JOB_KIND),
                ).fetchone()
                if chain is None:
                    raise F0FError("JOB_NOT_AVAILABLE")
                if (
                    chain["status"] != "RUNNING"
                    or not chain["lease_valid"]
                    or int(chain["lease_generation"]) != lease.generation
                    or chain["lease_token"] != lease.token
                    or chain["lease_owner"] != lease.worker_id
                    or not _is_opaque_name(chain["object_key"])
                    or chain["run_terminal_status"] != "CANDIDATE_EVIDENCE_RECORDED"
                    or str(chain["base_execution_profile_sha256"])
                    != str(chain["base_f0e_execution_profile_sha256"])
                ):
                    raise F0FError("JOB_LEASE_STALE")
                rows = connection.execute(
                    "SELECT u.*,p.page_count AS expected_total_pages,e.id AS page_evidence_id,"
                    "e.selected_route,e.terminal_status,e.render_sha256,e.output_sha256,"
                    "e.output_block_count,e.output_character_count,"
                    "e.output_non_blank_character_count,e.mean_confidence_ppm,"
                    "e.bbox_summary_sha256,e.reason_code,e.evidence_chain_sha256 "
                    "FROM f0d.document_processing_unit u "
                    "JOIN f0d.document_processing_plan p ON p.enterprise_id=u.enterprise_id "
                    "AND p.id=u.processing_plan_id "
                    "JOIN f0e.page_evidence_selection e ON e.enterprise_id=u.enterprise_id "
                    "AND e.local_ocr_run_id=%s AND e.processing_unit_id=u.id "
                    "AND e.processing_plan_id=u.processing_plan_id "
                    "WHERE u.enterprise_id=%s AND u.processing_plan_id=%s "
                    "ORDER BY u.unit_ordinal",
                    (
                        chain["local_ocr_run_id"],
                        context.enterprise_id,
                        chain["processing_plan_id"],
                    ),
                ).fetchall()
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

        mapped_units = []
        for row in rows:
            mapped = dict(row)
            mapped["native_text_sha256"] = mapped.get("native_text_identity_sha256")
            mapped_units.append(processing_unit_from_row(mapped))
        routes = build_page_routes(tuple(mapped_units))
        by_id = {row["id"]: row for row in rows}
        pages: list[BodyPageSource] = []
        for route in routes:
            row = by_id.get(route.processing_unit_id)
            if row is None or row["selected_route"] != route.evidence_method:
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            expected = OcrPageEvidence(
                evidence_id=row["page_evidence_id"],
                processing_unit_id=route.processing_unit_id,
                source_unit_id=route.source_unit_id,
                candidate_decision=route.candidate_decision,
                selected_route=str(row["selected_route"]),
                terminal_status=str(row["terminal_status"]),
                source_evidence_sha256=route.source_evidence_sha256,
                render_sha256=_optional_text(row["render_sha256"]),
                output_sha256=str(row["output_sha256"]),
                output_block_count=int(row["output_block_count"]),
                output_character_count=int(row["output_character_count"]),
                output_non_blank_characters=int(
                    row["output_non_blank_character_count"]
                ),
                mean_confidence_ppm=_optional_int(row["mean_confidence_ppm"]),
                bbox_summary_sha256=_optional_text(row["bbox_summary_sha256"]),
                reason_code=str(row["reason_code"]),
                execution_profile_sha256=str(
                    chain["base_execution_profile_sha256"]
                ),
            )
            pages.append(
                BodyPageSource(
                    page_evidence_id=row["page_evidence_id"],
                    evidence_chain_sha256=str(row["evidence_chain_sha256"]),
                    route=route,
                    expected_evidence=expected,
                )
            )
        if len(pages) != int(chain["visual_unit_count"]):
            raise F0FError("BODY_EVIDENCE_MISMATCH")
        return ControlledBodyExecution(
            lease=lease,
            processing_plan_id=chain["processing_plan_id"],
            document_version_id=chain["document_version_id"],
            source_document_id=str(chain["source_document_id"]),
            source_plan_sha256=str(chain["source_plan_sha256"]),
            local_ocr_run_id=chain["local_ocr_run_id"],
            local_ocr_output_manifest_sha256=str(chain["output_manifest_sha256"]),
            object_blob_id=chain["object_blob_id"],
            vault_object_id=str(chain["object_key"]),
            input_object_sha256=str(chain["input_object_sha256"]),
            input_object_size=int(chain["size_bytes"]),
            configuration=_configuration(chain),
            pages=tuple(pages),
        )

    def finalize(
        self,
        context: SessionContext,
        execution: ControlledBodyExecution,
        bodies: dict[uuid.UUID, BoundPageBody],
        key: LocalFixtureKey,
    ) -> int:
        if (
            not isinstance(execution, ControlledBodyExecution)
            or not isinstance(bodies, dict)
            or set(bodies) != {page.page_evidence_id for page in execution.pages}
            or not isinstance(key, LocalFixtureKey)
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        metadata: list[dict[str, object]] = []
        body_values: list[bytearray] = []
        key_material = bytearray(key.view())
        try:
            for index, page in enumerate(execution.pages, start=1):
                bound = bodies[page.page_evidence_id]
                if (
                    not isinstance(bound, BoundPageBody)
                    or bound.page_evidence_id != page.page_evidence_id
                    or bound.selected_route != page.route.evidence_method
                    or bound.source_output_sha256 != page.expected_evidence.output_sha256
                    or bound.source_page_evidence_sha256 != page.evidence_chain_sha256
                    or bound.body.byte_count > 4 * 1024 * 1024
                ):
                    raise F0FError("BODY_EVIDENCE_MISMATCH")
                body = bound.body
                if not isinstance(body, CanonicalBody):
                    raise F0FError("BODY_LIMIT_EXCEEDED")
                body_id = stable_uuid4(
                    "f0f-page-body",
                    page.page_evidence_id,
                    body.sha256,
                    execution.configuration.configuration_sha256,
                )
                metadata.append(
                    {
                        "body_evidence_id": str(body_id),
                        "page_evidence_id": str(page.page_evidence_id),
                        "body_index": index,
                        "plaintext_sha256": body.sha256,
                        "plaintext_size_bytes": body.byte_count,
                        "ocr_block_byte_lengths": (
                            None
                            if bound.ocr_block_byte_lengths is None
                            else list(bound.ocr_block_byte_lengths)
                        ),
                    }
                )
                body_values.append(bytearray(body.view()))
            audit_id = stable_uuid4(
                "f0f-body-audit", execution.lease.job_id, execution.lease.generation
            )
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                row = connection.execute(
                    "SELECT f0f.finalize_controlled_body_capture("
                    "%s,%s,%s,%s,%s,%s,%s) AS count",
                    (
                        execution.lease.job_id,
                        execution.lease.generation,
                        execution.lease.token,
                        audit_id,
                        Jsonb(metadata),
                        body_values,
                        key_material,
                    ),
                ).fetchone()
            if row is None or int(row["count"]) != len(execution.pages):
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            return int(row["count"])
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None
        finally:
            key_material[:] = b"\0" * len(key_material)
            key_material.clear()
            for value in body_values:
                value[:] = b"\0" * len(value)
                value.clear()
            for body in bodies.values():
                body.wipe()

    def decrypt_verified(
        self,
        context: SessionContext,
        body_evidence_id: uuid.UUID,
        key: LocalFixtureKey,
    ) -> CanonicalBody:
        if not isinstance(body_evidence_id, uuid.UUID) or not isinstance(
            key, LocalFixtureKey
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        key_material = bytearray(key.view())
        plaintext = bytearray()
        try:
            with tenant_transaction(self.config, "f0d_runtime", context) as connection:
                metadata = connection.execute(
                    "SELECT plaintext_sha256,plaintext_size_bytes,"
                    "plaintext_character_count,plaintext_non_blank_character_count,"
                    "normalization_rule FROM f0f.page_body_evidence "
                    "WHERE enterprise_id=%s AND id=%s",
                    (context.enterprise_id, body_evidence_id),
                ).fetchone()
                if metadata is None:
                    raise F0FError("BODY_DECRYPTION_FAILED")
                row = connection.execute(
                    "SELECT f0f.decrypt_verified_body(%s,%s) AS body",
                    (body_evidence_id, key_material),
                ).fetchone()
            if row is None:
                raise F0FError("BODY_DECRYPTION_FAILED")
            plaintext.extend(row["body"])
            body = CanonicalBody(
                plaintext,
                characters=int(metadata["plaintext_character_count"]),
                nonblank_characters=int(
                    metadata["plaintext_non_blank_character_count"]
                ),
                normalization_rule=str(metadata["normalization_rule"]),
            )
            if (
                body.sha256 != str(metadata["plaintext_sha256"])
                or body.byte_count != int(metadata["plaintext_size_bytes"])
            ):
                body.wipe()
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            return body
        except F0FError:
            raise
        except Exception:
            raise F0FError("BODY_DECRYPTION_FAILED") from None
        finally:
            key_material[:] = b"\0" * len(key_material)
            key_material.clear()
            plaintext[:] = b"\0" * len(plaintext)
            plaintext.clear()

    def enqueue_annotation_candidates(
        self, context: SessionContext
    ) -> tuple[uuid.UUID, ...]:
        try:
            with tenant_transaction(self.config, "f0d_worker", context) as connection:
                rows = connection.execute(
                    "SELECT b.id AS body_evidence_id,b.processing_unit_id,"
                    "b.source_document_id,b.source_unit_id,b.selected_route,r.source_group "
                    "FROM f0f.page_body_evidence b JOIN f0d.fixture_source_registry r "
                    "ON r.enterprise_id=b.enterprise_id "
                    "AND r.source_document_id=b.source_document_id "
                    "WHERE b.enterprise_id=%s ORDER BY b.source_document_id,b.unit_ordinal",
                    (context.enterprise_id,),
                ).fetchall()
                candidates = select_annotation_candidates(rows)
                by_unit = {row["processing_unit_id"]: row["body_evidence_id"] for row in rows}
                queue_ids = []
                for candidate in candidates:
                    row = connection.execute(
                        "SELECT f0f.enqueue_gold_annotation(%s,%s,%s) AS id",
                        (
                            candidate.queue_id,
                            by_unit[candidate.processing_unit_id],
                            candidate.queue_ordinal,
                        ),
                    ).fetchone()
                    if row is None or row["id"] != candidate.queue_id:
                        raise F0FError("BODY_EVIDENCE_MISMATCH")
                    queue_ids.append(candidate.queue_id)
                return tuple(queue_ids)
        except F0FError:
            raise
        except Exception:
            raise F0FError("DATABASE_OPERATION_FAILED") from None

    def record_gold_label(self, *_: object, **__: object) -> None:
        raise F0FError("GOLD_OPERATION_DENIED")

    def adjudicate_gold_labels(self, *_: object, **__: object) -> None:
        raise F0FError("GOLD_OPERATION_DENIED")


def _configuration(row: dict[str, object]) -> BodyConfigurationRecord:
    return BodyConfigurationRecord(
        configuration_id=row.get("controlled_body_configuration_id", row["id"]),
        configuration_sha256=str(
            row.get("controlled_body_configuration_sha256", row["configuration_sha256"])
        ),
        runner_image_id=str(row["runner_image_id"]),
        runner_lock_sha256=str(row["runner_lock_sha256"]),
        runner_profile_sha256=str(row["runner_profile_sha256"]),
        base_f0e_image_id=str(row["base_f0e_image_id"]),
        base_f0e_execution_profile_sha256=str(
            row["base_f0e_execution_profile_sha256"]
        ),
        runner_protocol=str(row["runner_protocol"]),
        max_plaintext_bytes=int(row["max_plaintext_bytes"]),
        timeout_seconds=int(row["timeout_seconds"]),
    )


def _eligible_run(row: dict[str, object], configuration: BodyConfigurationRecord) -> bool:
    return bool(
        row["terminal_status"] == "CANDIDATE_EVIDENCE_RECORDED"
        and int(row["visual_unit_count"]) > 0
        and int(row["visual_unit_count"])
        == int(row["native_reference_count"]) + int(row["local_ocr_count"])
        and int(row["deferred_document_count"]) == 0
        and row["benchmark_tier"] == "NONE"
        and row["external_processing_policy"] == "DENY"
        and not row["raw_text_persisted"]
        and not row["page_image_persisted"]
        and not row["plan_raw_text_persisted"]
        and not row["plan_ocr_executed"]
        and str(row["body_configuration_sha256"])
        == configuration.configuration_sha256
    )


def _validate_lease(lease: JobLease) -> None:
    if (
        not isinstance(lease, JobLease)
        or not isinstance(lease.job_id, uuid.UUID)
        or isinstance(lease.generation, bool)
        or not isinstance(lease.generation, int)
        or lease.generation <= 0
        or not isinstance(lease.token, uuid.UUID)
        or _SAFE_WORKER.fullmatch(lease.worker_id) is None
    ):
        raise F0FError("BODY_CONTRACT_INVALID")


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def bind_native_body(page: BodyPageSource, body: CanonicalBody) -> BoundPageBody:
    if (
        not isinstance(page, BodyPageSource)
        or page.route.evidence_method != "NATIVE_REFERENCE"
        or not isinstance(body, CanonicalBody)
    ):
        raise F0FError("BODY_CONTRACT_INVALID")
    return BoundPageBody(
        page_evidence_id=page.page_evidence_id,
        selected_route=page.route.evidence_method,
        source_output_sha256=page.expected_evidence.output_sha256,
        source_page_evidence_sha256=page.evidence_chain_sha256,
        body=body,
    )


def bind_ocr_body(page: BodyPageSource, result: OcrBodyResult) -> BoundPageBody:
    if (
        not isinstance(page, BodyPageSource)
        or page.route.evidence_method != "LOCAL_OCR"
        or not isinstance(result, OcrBodyResult)
        or result.f0e_text_sequence_sha256
        != page.expected_evidence.output_sha256
    ):
        raise F0FError("BODY_EVIDENCE_MISMATCH")
    return BoundPageBody(
        page_evidence_id=page.page_evidence_id,
        selected_route=page.route.evidence_method,
        source_output_sha256=page.expected_evidence.output_sha256,
        source_page_evidence_sha256=page.evidence_chain_sha256,
        body=result.body,
        ocr_block_byte_lengths=tuple(
            len(block.text.encode("utf-8", errors="strict"))
            for block in result.blocks
        ),
    )


__all__ = (
    "BodyConfigurationRecord",
    "BodyPageSource",
    "ControlledBodyExecution",
    "ControlledBodyService",
    "bind_native_body",
    "bind_ocr_body",
)
