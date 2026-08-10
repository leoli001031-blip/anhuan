"""Transactional enqueue, lease, heartbeat, load, and terminal F0-E service."""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from psycopg.types.json import Jsonb

from ..auth import SessionContext
from ..database import DatabaseConfig, tenant_transaction
from ..service import JobLease
from ..vault import _is_opaque_name
from .contracts import (
    DeferredDocumentRoute,
    F0EError,
    OcrPageEvidence,
    OcrRunEnvelope,
    PageRoute,
    ResourceLimits,
    SandboxProfile,
    require_sha256,
)
from .hashing import stable_uuid4
from .routing import (
    build_deferred_route,
    build_page_routes,
    native_reference_evidence,
    processing_unit_from_row,
)
from .sql_names import (
    CONFIGURATION_TABLE,
    FINALIZE_FUNCTION,
    IDEMPOTENCY_FUNCTION,
    JOB_KIND,
    JOB_TABLE,
    PLAN_TABLE,
    UNIT_TABLE,
)


_SAFE_WORKER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NORMALIZATION_RULE_SHA256 = (
    "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
)


@dataclass(frozen=True, slots=True)
class LocalOcrConfigurationRecord:
    configuration_id: uuid.UUID
    configuration_sha256: str
    renderer_id: str
    renderer_version: str
    renderer_binary_sha256: str
    ocr_engine_id: str
    ocr_engine_version: str
    ocr_engine_binary_sha256: str
    language_pack_ids: str
    language_pack_bundle_sha256: str
    normalization_profile_sha256: str
    execution_profile_sha256: str
    container_image_id: str
    lock_sha256: str
    dpi: int
    max_pdf_pages: int
    max_selected_pages_per_run: int
    max_pixels_per_page: int
    manual_review_confidence_floor_ppm: int
    timeout_seconds: int
    coordinate_space_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.configuration_id, uuid.UUID):
            raise F0EError("CONTRACT_INVALID")
        for digest in (
            self.configuration_sha256,
            self.renderer_binary_sha256,
            self.ocr_engine_binary_sha256,
            self.language_pack_bundle_sha256,
            self.normalization_profile_sha256,
            self.execution_profile_sha256,
            self.lock_sha256,
        ):
            require_sha256(digest)
        if (
            re.fullmatch(r"sha256:[0-9a-f]{64}", self.container_image_id) is None
            or self.renderer_id != "pypdfium2"
            or self.renderer_version != "5.12.1"
            or self.ocr_engine_id != "rapidocr-onnxruntime"
            or self.ocr_engine_version != "1.4.4"
            or self.normalization_profile_sha256 != _NORMALIZATION_RULE_SHA256
            or self.dpi != 250
            or self.max_pdf_pages != 128
            or self.max_selected_pages_per_run != 16
            or self.max_pixels_per_page != 16_000_000
            or self.manual_review_confidence_floor_ppm != 0
            or not 1 <= self.timeout_seconds <= 3600
            or self.coordinate_space_version != "RENDERED_PIXEL_TOP_LEFT_V1"
        ):
            raise F0EError("CONTRACT_INVALID")

    @property
    def sandbox_profile(self) -> SandboxProfile:
        return SandboxProfile(
            renderer_sha256=self.renderer_binary_sha256,
            ocr_engine_sha256=self.ocr_engine_binary_sha256,
            language_pack_sha256=self.language_pack_bundle_sha256,
            execution_profile_sha256=self.execution_profile_sha256,
            normalization_rule_sha256=self.normalization_profile_sha256,
            render_dpi=self.dpi,
            manual_review_confidence_floor_ppm=(
                self.manual_review_confidence_floor_ppm
            ),
            container_image_id=self.container_image_id,
        )

    @property
    def resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            timeout_ms=self.timeout_seconds * 1000,
            maximum_pages=self.max_pdf_pages,
            maximum_selected_pages_per_run=self.max_selected_pages_per_run,
            render_dpi=self.dpi,
            maximum_pixels=self.max_pixels_per_page,
        )


@dataclass(frozen=True, slots=True)
class LocalOcrExecution:
    lease: JobLease
    processing_plan_id: uuid.UUID
    document_version_id: uuid.UUID
    object_blob_id: uuid.UUID
    vault_object_id: str
    input_object_sha256: str
    input_object_size: int
    source_plan_sha256: str
    input_version: str
    document_type: str
    configuration: LocalOcrConfigurationRecord
    routes: tuple[PageRoute, ...]
    deferred_document: DeferredDocumentRoute | None

    @property
    def native_evidence(self) -> tuple[OcrPageEvidence, ...]:
        profile = self.configuration.sandbox_profile
        return tuple(
            native_reference_evidence(route, profile)
            for route in self.routes
            if route.evidence_method == "NATIVE_REFERENCE"
        )

    @property
    def ocr_routes(self) -> tuple[PageRoute, ...]:
        return tuple(
            route for route in self.routes if route.evidence_method == "LOCAL_OCR"
        )


class LocalOcrService:
    def __init__(self, config: DatabaseConfig) -> None:
        if not isinstance(config, DatabaseConfig):
            raise F0EError("CONTRACT_INVALID")
        self.config = config

    def enqueue(
        self,
        context: SessionContext,
        processing_plan_id: uuid.UUID,
        configuration_id: uuid.UUID,
    ) -> uuid.UUID:
        if not isinstance(processing_plan_id, uuid.UUID) or not isinstance(
            configuration_id, uuid.UUID
        ):
            raise F0EError("CONTRACT_INVALID")
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT p.id,p.document_version_id,p.source_plan_sha256,"
                        "p.page_count,p.visual_unit_count,p.native_candidate_count,"
                        "p.ocr_required_count,p.manual_review_count,p.deferred_conversion,"
                        "p.raw_text_persisted,p.ocr_executed,p.benchmark_tier,"
                        "p.external_processing_policy,r.document_type,r.source_group,"
                        "r.enterprise_fact_allowed,r.current_regulation_allowed,"
                        "r.search_publish_allowed,r.benchmark_tier AS source_benchmark_tier,"
                        "r.external_processing_policy AS source_external_policy,"
                        "b.size_bytes,c.id AS configuration_id,"
                        "c.configuration_sha256,c.max_pdf_pages,"
                        "c.max_selected_pages_per_run,c.max_pixels_per_page,"
                        "c.manual_review_confidence_floor_ppm,c.network_policy,"
                        "c.external_processing_policy AS config_external_policy,"
                        "c.benchmark_tier AS config_benchmark_tier,c.raw_text_persisted "
                        "AS config_raw_text_persisted,c.page_image_persisted "
                        f"AS config_page_image_persisted FROM {PLAN_TABLE} p "
                        "JOIN f0d.document_version v ON v.enterprise_id=p.enterprise_id "
                        "AND v.id=p.document_version_id AND v.source_document_id=p.source_document_id "
                        "JOIN f0d.object_blob b ON b.enterprise_id=v.enterprise_id "
                        "AND b.id=v.object_blob_id AND b.upload_session_id=v.upload_session_id "
                        "JOIN f0d.upload_session u ON u.enterprise_id=v.enterprise_id "
                        "AND u.id=v.upload_session_id AND u.status='COMPLETED' "
                        "AND u.captured_sha256=b.sha256 AND u.captured_size_bytes=b.size_bytes "
                        "JOIN f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                        "AND r.source_document_id=p.source_document_id "
                        "AND r.expected_sha256=b.sha256 AND r.expected_size_bytes=b.size_bytes "
                        f"JOIN {CONFIGURATION_TABLE} c ON c.enterprise_id=p.enterprise_id "
                        "AND c.id=%s WHERE p.enterprise_id=%s AND p.id=%s "
                        "",
                        (configuration_id, context.enterprise_id, processing_plan_id),
                    )
                    chain = cursor.fetchone()
                    if chain is None:
                        raise F0EError("PLAN_NOT_AVAILABLE")
                    _validate_enqueue_chain(chain)
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (f"LOCAL_OCR:{context.enterprise_id}:{processing_plan_id}",),
                    )
                    cursor.execute(
                        f"SELECT id,document_version_id,source_plan_sha256,"
                        "local_ocr_configuration_id,local_ocr_configuration_sha256,"
                        "input_version,progress_total "
                        f"FROM {JOB_TABLE} WHERE enterprise_id=%s "
                        "AND kind=%s AND processing_plan_id=%s",
                        (context.enterprise_id, JOB_KIND, processing_plan_id),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        expected_input_version = (
                            f"{chain['source_plan_sha256']}:"
                            f"{chain['configuration_sha256']}"
                        )
                        if (
                            existing["document_version_id"]
                            != chain["document_version_id"]
                            or str(existing["source_plan_sha256"])
                            != str(chain["source_plan_sha256"])
                            or existing["local_ocr_configuration_id"]
                            != configuration_id
                            or str(existing["local_ocr_configuration_sha256"])
                            != str(chain["configuration_sha256"])
                            or existing["input_version"] != expected_input_version
                            or existing["progress_total"]
                            != chain["visual_unit_count"]
                        ):
                            raise F0EError("EVIDENCE_MISMATCH")
                        return existing["id"]

                    job_id = stable_uuid4(
                        "job",
                        JOB_KIND,
                        context.enterprise_id,
                        processing_plan_id,
                        configuration_id,
                        str(chain["configuration_sha256"]),
                    )
                    trace_id = stable_uuid4("trace", job_id)
                    input_version = (
                        f"{chain['source_plan_sha256']}:"
                        f"{chain['configuration_sha256']}"
                    )
                    cursor.execute(
                        f"INSERT INTO {JOB_TABLE}(id,enterprise_id,kind,"
                        "document_version_id,processing_plan_id,source_plan_sha256,"
                        "local_ocr_configuration_id,local_ocr_configuration_sha256,"
                        "idempotency_key,input_version,progress_total,trace_id) VALUES "
                        f"(%s,%s,%s,%s,%s,%s,%s,%s,{IDEMPOTENCY_FUNCTION}(%s,%s,%s,%s,%s),"
                        "%s,%s,%s)",
                        (
                            job_id,
                            context.enterprise_id,
                            JOB_KIND,
                            chain["document_version_id"],
                            processing_plan_id,
                            chain["source_plan_sha256"],
                            configuration_id,
                            chain["configuration_sha256"],
                            context.enterprise_id,
                            processing_plan_id,
                            str(chain["source_plan_sha256"]),
                            configuration_id,
                            str(chain["configuration_sha256"]),
                            input_version,
                            chain["visual_unit_count"],
                            trace_id,
                        ),
                    )
                    return job_id
        except F0EError:
            raise
        except Exception:
            raise F0EError("DATABASE_OPERATION_FAILED") from None

    def claim(
        self, context: SessionContext, worker_id: str
    ) -> JobLease | None:
        if not isinstance(worker_id, str) or _SAFE_WORKER.fullmatch(worker_id) is None:
            raise F0EError("CONTRACT_INVALID")
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT j.id,c.timeout_seconds FROM {JOB_TABLE} j "
                        f"JOIN {CONFIGURATION_TABLE} c ON c.enterprise_id=j.enterprise_id "
                        "AND c.id=j.local_ocr_configuration_id "
                        "AND c.configuration_sha256=j.local_ocr_configuration_sha256 "
                        "WHERE j.enterprise_id=%s AND j.kind=%s AND j.attempts<100 AND "
                        "((j.status='PENDING' AND j.run_after<=statement_timestamp()) OR "
                        "(j.status='RUNNING' AND j.lease_until<statement_timestamp())) "
                        "ORDER BY j.priority,j.created_at,j.id FOR UPDATE OF j "
                        "SKIP LOCKED LIMIT 1",
                        (context.enterprise_id, JOB_KIND),
                    )
                    job = cursor.fetchone()
                    if job is None:
                        return None
                    token = uuid.uuid4()
                    lease_seconds = int(job["timeout_seconds"]) + 30
                    cursor.execute(
                        f"UPDATE {JOB_TABLE} SET status='RUNNING',attempts=attempts+1,"
                        "lease_owner=%s,lease_until=statement_timestamp()+%s*interval '1 second',"
                        "lease_generation=lease_generation+1,lease_token=%s,"
                        "heartbeat_at=statement_timestamp(),error_code=NULL "
                        "WHERE enterprise_id=%s AND id=%s AND kind=%s "
                        "RETURNING lease_generation",
                        (
                            worker_id,
                            lease_seconds,
                            token,
                            context.enterprise_id,
                            job["id"],
                            JOB_KIND,
                        ),
                    )
                    claimed = cursor.fetchone()
                    if claimed is None:
                        raise F0EError("JOB_NOT_AVAILABLE")
                    return JobLease(
                        job_id=job["id"],
                        generation=int(claimed["lease_generation"]),
                        token=token,
                        worker_id=worker_id,
                    )
        except F0EError:
            raise
        except Exception:
            raise F0EError("DATABASE_OPERATION_FAILED") from None

    def heartbeat(
        self,
        context: SessionContext,
        lease: JobLease,
        done: int,
        total: int,
    ) -> None:
        _validate_lease(lease)
        if (
            isinstance(done, bool)
            or not isinstance(done, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or done < 0
            or total < 0
            or done > total
        ):
            raise F0EError("CONTRACT_INVALID")
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE {JOB_TABLE} j SET heartbeat_at=statement_timestamp(),"
                        "lease_until=statement_timestamp()+(c.timeout_seconds+30)*interval '1 second',"
                        "progress_done=%s "
                        f"FROM {CONFIGURATION_TABLE} c WHERE j.enterprise_id=%s "
                        "AND j.id=%s AND j.kind=%s AND j.status='RUNNING' "
                        "AND j.lease_owner=%s AND j.lease_generation=%s "
                        "AND j.lease_token=%s AND j.lease_until>statement_timestamp() "
                        "AND j.progress_total=%s "
                        "AND c.enterprise_id=j.enterprise_id "
                        "AND c.id=j.local_ocr_configuration_id "
                        "AND c.configuration_sha256=j.local_ocr_configuration_sha256",
                        (
                            done,
                            context.enterprise_id,
                            lease.job_id,
                            JOB_KIND,
                            lease.worker_id,
                            lease.generation,
                            lease.token,
                            total,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise F0EError("JOB_LEASE_STALE")
        except F0EError:
            raise
        except Exception:
            raise F0EError("DATABASE_OPERATION_FAILED") from None

    def load_execution(
        self, context: SessionContext, lease: JobLease
    ) -> LocalOcrExecution:
        return self._load_execution(context, lease, allow_succeeded=False)

    def finalize(
        self,
        context: SessionContext,
        lease: JobLease,
        envelope: OcrRunEnvelope,
    ) -> uuid.UUID:
        _validate_lease(lease)
        if not isinstance(envelope, OcrRunEnvelope):
            raise F0EError("CONTRACT_INVALID")
        execution = self._load_execution(context, lease, allow_succeeded=True)
        payload, deferred_id = _finalize_payload(execution, envelope)
        audit_id = stable_uuid4("audit", "LOCAL_OCR_EVIDENCE_FINALIZED", envelope.run_id)
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {FINALIZE_FUNCTION}(%s,%s,%s,%s,%s,%s,%s) AS run_id",
                        (
                            lease.job_id,
                            lease.generation,
                            lease.token,
                            envelope.run_id,
                            audit_id,
                            Jsonb(payload),
                            deferred_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None or row["run_id"] != envelope.run_id:
                        raise F0EError("EVIDENCE_MISMATCH")
                    return row["run_id"]
        except F0EError:
            raise
        except Exception:
            raise F0EError("DATABASE_OPERATION_FAILED") from None

    def _load_execution(
        self,
        context: SessionContext,
        lease: JobLease,
        *,
        allow_succeeded: bool,
    ) -> LocalOcrExecution:
        _validate_lease(lease)
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT j.status,j.lease_generation,j.lease_token,"
                        "j.lease_owner,j.lease_until>statement_timestamp() AS lease_valid,"
                        "j.processing_plan_id,j.document_version_id,j.source_plan_sha256,"
                        "j.local_ocr_configuration_id,j.local_ocr_configuration_sha256,"
                        "j.input_version,p.page_count,p.visual_unit_count,"
                        "p.native_candidate_count,p.ocr_required_count,p.manual_review_count,"
                        "p.deferred_conversion,p.raw_text_persisted AS plan_raw_text_persisted,"
                        "p.ocr_executed AS plan_ocr_executed,"
                        "p.benchmark_tier AS plan_benchmark_tier,"
                        "p.external_processing_policy AS plan_external_policy,"
                        "v.object_blob_id,b.object_key,"
                        "b.sha256 AS input_object_sha256,b.size_bytes AS input_object_size,"
                        "r.document_type,r.source_group,r.enterprise_fact_allowed,"
                        "r.current_regulation_allowed,r.search_publish_allowed,"
                        "r.benchmark_tier AS source_benchmark_tier,"
                        "r.external_processing_policy AS source_external_policy,c.* "
                        f"FROM {JOB_TABLE} j JOIN {PLAN_TABLE} p "
                        "ON p.enterprise_id=j.enterprise_id AND p.id=j.processing_plan_id "
                        "AND p.document_version_id=j.document_version_id "
                        "AND p.source_plan_sha256=j.source_plan_sha256 "
                        "JOIN f0d.document_version v ON v.enterprise_id=p.enterprise_id "
                        "AND v.id=p.document_version_id AND v.source_document_id=p.source_document_id "
                        "JOIN f0d.object_blob b ON b.enterprise_id=v.enterprise_id "
                        "AND b.id=v.object_blob_id AND b.upload_session_id=v.upload_session_id "
                        "JOIN f0d.upload_session u ON u.enterprise_id=v.enterprise_id "
                        "AND u.id=v.upload_session_id AND u.status='COMPLETED' "
                        "AND u.captured_sha256=b.sha256 AND u.captured_size_bytes=b.size_bytes "
                        "JOIN f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                        "AND r.source_document_id=p.source_document_id "
                        "AND r.expected_sha256=b.sha256 AND r.expected_size_bytes=b.size_bytes "
                        f"JOIN {CONFIGURATION_TABLE} c ON c.enterprise_id=j.enterprise_id "
                        "AND c.id=j.local_ocr_configuration_id "
                        "AND c.configuration_sha256=j.local_ocr_configuration_sha256 "
                        "WHERE j.enterprise_id=%s AND j.id=%s AND j.kind=%s",
                        (context.enterprise_id, lease.job_id, JOB_KIND),
                    )
                    chain = cursor.fetchone()
                    if chain is None:
                        raise F0EError("JOB_NOT_AVAILABLE")
                    if (
                        int(chain["lease_generation"]) != lease.generation
                        or chain["lease_token"] != lease.token
                        or (
                            chain["status"] == "RUNNING"
                            and (
                                not chain["lease_valid"]
                                or chain["lease_owner"] != lease.worker_id
                            )
                        )
                        or (
                            chain["status"] == "SUCCEEDED" and not allow_succeeded
                        )
                        or chain["status"] not in {"RUNNING", "SUCCEEDED"}
                    ):
                        raise F0EError("JOB_LEASE_STALE")
                    _validate_loaded_chain(chain)
                    cursor.execute(
                        f"SELECT u.*,p.page_count AS expected_total_pages "
                        f"FROM {UNIT_TABLE} u JOIN {PLAN_TABLE} p "
                        "ON p.enterprise_id=u.enterprise_id AND p.id=u.processing_plan_id "
                        "WHERE u.enterprise_id=%s AND u.processing_plan_id=%s "
                        "ORDER BY u.unit_ordinal",
                        (context.enterprise_id, chain["processing_plan_id"]),
                    )
                    units = []
                    for row in cursor.fetchall():
                        mapped = dict(row)
                        mapped["native_text_sha256"] = mapped.get(
                            "native_text_identity_sha256"
                        )
                        units.append(processing_unit_from_row(mapped))
        except F0EError:
            raise
        except Exception:
            raise F0EError("DATABASE_OPERATION_FAILED") from None

        routes = build_page_routes(tuple(units))
        if (
            len(routes) != int(chain["visual_unit_count"])
            or sum(route.evidence_method == "NATIVE_REFERENCE" for route in routes)
            != int(chain["native_candidate_count"])
            or sum(route.evidence_method == "LOCAL_OCR" for route in routes)
            != int(chain["ocr_required_count"])
            or any(route.evidence_method == "MANUAL_REVIEW_REFERENCE" for route in routes)
        ):
            raise F0EError("EVIDENCE_MISMATCH")
        configuration = _configuration(chain)
        deferred = None
        if bool(chain["deferred_conversion"]):
            deferred = build_deferred_route(
                chain["processing_plan_id"], chain["document_version_id"]
            )
        return LocalOcrExecution(
            lease=lease,
            processing_plan_id=chain["processing_plan_id"],
            document_version_id=chain["document_version_id"],
            object_blob_id=chain["object_blob_id"],
            vault_object_id=str(chain["object_key"]),
            input_object_sha256=str(chain["input_object_sha256"]),
            input_object_size=int(chain["input_object_size"]),
            source_plan_sha256=str(chain["source_plan_sha256"]),
            input_version=str(chain["input_version"]),
            document_type=str(chain["document_type"]),
            configuration=configuration,
            routes=routes,
            deferred_document=deferred,
        )


def _validate_enqueue_chain(chain: dict[str, object]) -> None:
    visual = int(chain["visual_unit_count"])
    deferred = bool(chain["deferred_conversion"])
    document_type = str(chain["document_type"])
    if (
        int(chain["manual_review_count"]) != 0
        or bool(chain["raw_text_persisted"])
        or bool(chain["ocr_executed"])
        or chain["benchmark_tier"] != "NONE"
        or chain["external_processing_policy"] != "DENY"
        or chain["source_benchmark_tier"] != "NONE"
        or chain["source_external_policy"] != "DENY"
        or chain["config_benchmark_tier"] != "NONE"
        or chain["config_external_policy"] != "DENY"
        or chain["network_policy"] != "DENY"
        or bool(chain["config_raw_text_persisted"])
        or bool(chain["config_page_image_persisted"])
        or bool(chain["current_regulation_allowed"])
        or bool(chain["search_publish_allowed"])
        or (chain["source_group"] == "negative" and chain["enterprise_fact_allowed"])
        or int(chain["manual_review_confidence_floor_ppm"]) != 0
        or int(chain["size_bytes"]) < 8
        or int(chain["size_bytes"]) > 64 * 1024 * 1024
        or int(chain["ocr_required_count"])
        > int(chain["max_selected_pages_per_run"])
        or int(chain["page_count"]) > int(chain["max_pdf_pages"])
    ):
        raise F0EError("PLAN_NOT_AVAILABLE")
    if not (
        (visual > 0 and not deferred and document_type in {"PDF", "JPEG"})
        or (visual == 0 and deferred and document_type == "DOC")
    ):
        raise F0EError("PLAN_NOT_AVAILABLE")


def _validate_loaded_chain(chain: dict[str, object]) -> None:
    _validate_enqueue_chain(
        {
            **chain,
            "size_bytes": chain["input_object_size"],
            "raw_text_persisted": chain["plan_raw_text_persisted"],
            "ocr_executed": chain["plan_ocr_executed"],
            "benchmark_tier": chain["plan_benchmark_tier"],
            "external_processing_policy": chain["plan_external_policy"],
            "config_benchmark_tier": chain["benchmark_tier"],
            "config_external_policy": chain["external_processing_policy"],
            "config_raw_text_persisted": chain["raw_text_persisted"],
            "config_page_image_persisted": chain["page_image_persisted"],
        }
    )
    if not _is_opaque_name(chain.get("object_key")):
        raise F0EError("EVIDENCE_MISMATCH")


def _configuration(chain: dict[str, object]) -> LocalOcrConfigurationRecord:
    return LocalOcrConfigurationRecord(
        configuration_id=chain["local_ocr_configuration_id"],
        configuration_sha256=str(chain["local_ocr_configuration_sha256"]),
        renderer_id=str(chain["renderer_id"]),
        renderer_version=str(chain["renderer_version"]),
        renderer_binary_sha256=str(chain["renderer_binary_sha256"]),
        ocr_engine_id=str(chain["ocr_engine_id"]),
        ocr_engine_version=str(chain["ocr_engine_version"]),
        ocr_engine_binary_sha256=str(chain["ocr_engine_binary_sha256"]),
        language_pack_ids=str(chain["language_pack_ids"]),
        language_pack_bundle_sha256=str(chain["language_pack_bundle_sha256"]),
        normalization_profile_sha256=str(chain["normalization_profile_sha256"]),
        execution_profile_sha256=str(chain["execution_profile_sha256"]),
        container_image_id=str(chain["container_image_id"]),
        lock_sha256=str(chain["lock_sha256"]),
        dpi=int(chain["dpi"]),
        max_pdf_pages=int(chain["max_pdf_pages"]),
        max_selected_pages_per_run=int(chain["max_selected_pages_per_run"]),
        max_pixels_per_page=int(chain["max_pixels_per_page"]),
        manual_review_confidence_floor_ppm=int(
            chain["manual_review_confidence_floor_ppm"]
        ),
        timeout_seconds=int(chain["timeout_seconds"]),
        coordinate_space_version=str(chain["coordinate_space_version"]),
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
        raise F0EError("CONTRACT_INVALID")


def _finalize_payload(
    execution: LocalOcrExecution, envelope: OcrRunEnvelope
) -> tuple[list[dict[str, object]], uuid.UUID | None]:
    if (
        envelope.processing_plan_id != execution.processing_plan_id
        or envelope.configuration_id
        != execution.configuration.configuration_id
        or envelope.input_version != execution.input_version
    ):
        raise F0EError("EVIDENCE_MISMATCH")
    if execution.deferred_document is not None:
        if (
            envelope.status != "DEFERRED_CONVERSION_REQUIRED"
            or envelope.page_evidence
            or envelope.deferred_documents != (execution.deferred_document,)
        ):
            raise F0EError("EVIDENCE_MISMATCH")
        return [], stable_uuid4(
            "deferred-evidence",
            envelope.run_id,
            execution.deferred_document.route_sha256,
        )
    if (
        envelope.status != "CANDIDATE_EVIDENCE_RECORDED"
        or envelope.deferred_documents
        or len(envelope.page_evidence) != len(execution.routes)
    ):
        raise F0EError("EVIDENCE_MISMATCH")
    by_unit = {item.processing_unit_id: item for item in envelope.page_evidence}
    if len(by_unit) != len(envelope.page_evidence):
        raise F0EError("EVIDENCE_MISMATCH")
    payload: list[dict[str, object]] = []
    for route in execution.routes:
        item = by_unit.get(route.processing_unit_id)
        if item is None or (
            item.source_unit_id != route.source_unit_id
            or item.candidate_decision != route.candidate_decision
            or item.selected_route != route.evidence_method
            or item.source_evidence_sha256 != route.source_evidence_sha256
            or item.execution_profile_sha256
            != execution.configuration.execution_profile_sha256
        ):
            raise F0EError("EVIDENCE_MISMATCH")
        if route.evidence_method == "NATIVE_REFERENCE":
            expected = native_reference_evidence(
                route, execution.configuration.sandbox_profile
            )
            if item != expected:
                raise F0EError("EVIDENCE_MISMATCH")
        elif route.evidence_method == "LOCAL_OCR":
            if item.evidence_id != stable_uuid4(
                "page-evidence",
                route.processing_plan_id,
                route.processing_unit_id,
                execution.configuration.execution_profile_sha256,
                item.render_sha256,
                item.output_sha256,
            ):
                raise F0EError("EVIDENCE_MISMATCH")
        else:
            raise F0EError("EVIDENCE_MISMATCH")
        payload.append(item.to_finalize_payload())
    return payload, None


__all__ = (
    "LocalOcrConfigurationRecord",
    "LocalOcrExecution",
    "LocalOcrService",
)
