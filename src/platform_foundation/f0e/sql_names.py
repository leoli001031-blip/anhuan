"""Centralized F0-E SQL identifiers and state literals."""

SCHEMA = "f0e"
CONFIGURATION_TABLE = "f0e.local_ocr_configuration"
RUN_TABLE = "f0e.local_ocr_run"
PAGE_EVIDENCE_TABLE = "f0e.page_evidence_selection"
DEFERRED_EVIDENCE_TABLE = "f0e.deferred_document_evidence"
JOB_TABLE = "f0d.job"
PLAN_TABLE = "f0d.document_processing_plan"
UNIT_TABLE = "f0d.document_processing_unit"
FINALIZE_FUNCTION = "f0e.finalize_local_ocr_run"
IDEMPOTENCY_FUNCTION = "f0e.local_ocr_job_idempotency_key"

JOB_KIND = "EXECUTE_LOCAL_OCR"
RUN_STATUS = "CANDIDATE_EVIDENCE_RECORDED"
DEFERRED_STATUS = "DEFERRED_CONVERSION_REQUIRED"

__all__ = (
    "CONFIGURATION_TABLE",
    "DEFERRED_EVIDENCE_TABLE",
    "DEFERRED_STATUS",
    "FINALIZE_FUNCTION",
    "IDEMPOTENCY_FUNCTION",
    "JOB_KIND",
    "JOB_TABLE",
    "PAGE_EVIDENCE_TABLE",
    "PLAN_TABLE",
    "RUN_STATUS",
    "RUN_TABLE",
    "SCHEMA",
    "UNIT_TABLE",
)
