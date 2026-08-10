"""F1 SQLAlchemy models for the f1.* schema (platform shell + workflow)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

ROLES = (
    "super_admin",
    "enterprise_admin",
    "plant_admin",
    "partner",
    "auditor",
)

DOC_STATUSES = ("pending", "scanning", "indexing", "done", "failed")
TASK_STATUSES = DOC_STATUSES
SERVICE_CASE_STATUSES = (
    "planned",
    "in_progress",
    "completed",
    "closed",
    "cancelled",
)
SERVICE_ASSIGNMENT_CAPACITIES = ("employee", "consultant", "partner")
SERVICE_ASSIGNMENT_STATUSES = ("pending", "accepted", "rejected", "revoked")
SITE_VISIT_STATUSES = ("planned", "in_progress", "completed", "cancelled")
FINDING_SEVERITIES = ("low", "medium", "high", "critical")
FINDING_STATUSES = (
    "open",
    "rectifying",
    "submitted",
    "reviewing",
    "passed",
    "rejected",
    "closed",
)
FINDING_REVIEW_DECISIONS = ("passed", "rejected")
INGESTION_PIPELINES = ("fixture_index", "controlled_ingestion")
INGESTION_STAGES = (
    "received",
    "scanning",
    "validating",
    "previewing",
    "ready",
    "retry_wait",
    "rejected",
    "failed",
)
INGESTION_SCAN_VERDICTS = (
    "not_required",
    "queued",
    "scanning",
    "clean",
    "infected",
    "error",
    "unavailable",
)
INGESTION_PREVIEW_STATUSES = (
    "not_required",
    "blocked",
    "queued",
    "generating",
    "ready",
    "failed",
)
CRM_ACCOUNT_STAGES = ("lead", "active", "dormant", "closed")
CRM_CONTACT_STATUSES = ("active", "inactive")
CRM_FOLLOW_UP_CHANNELS = ("onsite", "meeting", "phone", "internal_note")
BUSINESS_REPORT_STATUSES = ("active", "archived")
BUSINESS_REPORT_VERSION_LIFECYCLES = ("current", "superseded", "void")
POLICY_SOURCE_TYPES = ("law", "regulation", "standard", "guidance", "internal")
POLICY_DOMAINS = ("safety", "health", "environment", "fire", "chemical", "general")
POLICY_EFFECT_STATUSES = ("unknown", "not_effective", "effective", "expired")
POLICY_WORKFLOW_STATUSES = (
    "draft",
    "in_review",
    "approved",
    "rejected",
    "published",
    "superseded",
)
POLICY_IMPACT_PRIORITIES = ("low", "medium", "high", "critical")
QUALITY_SUITE_CATEGORIES = (
    "ingestion",
    "retrieval",
    "qa",
    "authorization",
    "injection",
)
QUALITY_SCENARIO_TYPES = (
    "exact_match",
    "threshold",
    "refusal_required",
    "isolation_required",
    "injection_blocked",
    "disagreement_max",
)
QUALITY_SEVERITIES = ("low", "medium", "high", "critical")
QUALITY_RUN_STATUSES = ("queued", "running", "passed", "failed", "cancelled")
QUALITY_RESULT_STATUSES = ("passed", "failed", "error")
QUALITY_DISAGREEMENT_KINDS = (
    "parser",
    "ocr",
    "citation",
    "refusal",
    "authorization",
    "injection",
)
QUALITY_DISAGREEMENT_REVIEW_STATUSES = ("open", "acknowledged", "waived")
REHEARSAL_PLAN_STATUSES = ("draft", "active", "archived")
REHEARSAL_CHECK_CATEGORIES = (
    "service",
    "dependency",
    "backup",
    "restore",
    "security",
    "rollback",
)
REHEARSAL_RUN_STATUSES = ("planned", "running", "passed", "failed", "cancelled")
REHEARSAL_RESULT_STATUSES = ("pending", "passed", "failed", "blocked")


class Enterprise(Base):
    __tablename__ = "enterprise"
    __table_args__ = {"schema": "f1"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    license_no: Mapped[str] = mapped_column(String(64))
    f0i_enterprise_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class Plant(Base):
    __tablename__ = "plant"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="plant_enterprise_id_uq"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = {"schema": "f1"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    keycloak_sub: Mapped[str] = mapped_column(String, unique=True)
    email: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class EnterpriseUser(Base):
    __tablename__ = "enterprise_user"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "user_id", name="uq_enterprise_user"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.user_profile.id"))
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="document_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "plant_id"),
            ("f1.plant.enterprise_id", "f1.plant.id"),
            name="document_plant_enterprise_fk",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    plant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    object_key: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "f1"}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"), nullable=True)
    user_sub: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    resource_type: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class UploadTask(Base):
    __tablename__ = "upload_task"
    __table_args__ = (
        Index(
            "upload_task_p3_document_uq",
            "enterprise_id",
            "document_id",
            unique=True,
            postgresql_where=text("pipeline_kind = 'controlled_ingestion'"),
        ),
        Index(
            "upload_task_fixture_sha_uq",
            "enterprise_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("pipeline_kind = 'fixture_index'"),
        ),
        UniqueConstraint("enterprise_id", "id", name="upload_task_enterprise_id_id_uq"),
        UniqueConstraint(
            "enterprise_id",
            "id",
            "document_id",
            name="upload_task_enterprise_id_id_document_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "document_id"),
            ("f1.document.enterprise_id", "f1.document.id"),
            name="upload_task_document_enterprise_fk",
        ),
        CheckConstraint(
            "object_state IN ('reserved','quarantined','ready','write_failed')",
            name="upload_task_object_state_ck",
        ),
        CheckConstraint(
            "pipeline_kind <> 'controlled_ingestion' OR "
            "((object_state='reserved' AND quarantine_status='held' "
            "AND released_at IS NULL AND rejected_at IS NULL) OR "
            "(object_state='write_failed' AND quarantine_status IN "
            "('not_applicable','blocked') AND released_at IS NULL) OR "
            "(object_state='quarantined' AND quarantine_status IN ('held','blocked') "
            "AND released_at IS NULL) OR "
            "(object_state='ready' AND status='done' AND processing_stage='ready' "
            "AND scan_verdict='clean' AND preview_status='ready' "
            "AND quarantine_status IN ('held','released')))",
            name="upload_task_p3_state_ck",
        ),
        CheckConstraint(
            "pipeline_kind <> 'controlled_ingestion' OR "
            "((released_at IS NULL AND quarantine_status <> 'released') OR "
            "(released_at IS NOT NULL AND object_state='ready' "
            "AND quarantine_status='released' AND processing_stage='ready' "
            "AND scan_verdict='clean' AND preview_status='ready'))",
            name="upload_task_p3_release_ck",
        ),
        CheckConstraint(
            "pipeline_kind <> 'controlled_ingestion' OR rejected_at IS NULL OR "
            "(object_state='quarantined' AND quarantine_status='blocked' "
            "AND processing_stage='rejected' AND status='failed' "
            "AND released_at IS NULL)",
            name="upload_task_p3_reject_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    object_key: Mapped[str] = mapped_column(String)
    content_sha256: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_state: Mapped[str] = mapped_column(String, default="reserved")
    source_etag: Mapped[str | None] = mapped_column(String, nullable=True)
    source_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_kind: Mapped[str] = mapped_column(String, default="fixture_index")
    processing_stage: Mapped[str] = mapped_column(String, default="received")
    quarantine_status: Mapped[str] = mapped_column(
        String, default="not_applicable"
    )
    scan_verdict: Mapped[str] = mapped_column(String, default="not_required")
    scanner_engine: Mapped[str | None] = mapped_column(String, nullable=True)
    scanner_version: Mapped[str | None] = mapped_column(String, nullable=True)
    signature_version: Mapped[str | None] = mapped_column(String, nullable=True)
    preview_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    preview_status: Mapped[str] = mapped_column(String, default="not_required")
    preview_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    preview_unit_count: Mapped[int] = mapped_column(Integer, default=0)
    resource_policy_version: Mapped[str] = mapped_column(
        String, default="fixture-v1"
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("task_id", "event_type", name="outbox_task_idem_uq"),
        UniqueConstraint("rq_job_id", name="outbox_rq_job_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "task_id"),
            ("f1.upload_task.enterprise_id", "f1.upload_task.id"),
            name="outbox_task_enterprise_fk",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    event_type: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="pending")
    payload_sha256: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rq_job_id: Mapped[str] = mapped_column(String)
    dispatch_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dispatch_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempt: Mapped[int] = mapped_column(Integer, default=0)


class QaRequest(Base):
    __tablename__ = "qa_request"
    __table_args__ = {"schema": "f1"}

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    question_sha256: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="accepted")
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    owner_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)


class InviteJti(Base):
    __tablename__ = "invite_jti"
    __table_args__ = {"schema": "f1"}

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    email: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_by_sub: Mapped[str | None] = mapped_column(String, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())


class ServiceCase(Base):
    __tablename__ = "service_case"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="service_case_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "plant_id"),
            ("f1.plant.enterprise_id", "f1.plant.id"),
            name="service_case_plant_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="service_case_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('planned','in_progress','completed','closed','cancelled')",
            name="service_case_status_ck",
        ),
        CheckConstraint(
            "planned_start_at IS NULL OR planned_end_at IS NULL "
            "OR planned_end_at >= planned_start_at",
            name="service_case_planned_window_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    plant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String, default="planned")
    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class ServiceAssignment(Base):
    __tablename__ = "service_assignment"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="service_assignment_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "service_case_id"),
            ("f1.service_case.enterprise_id", "f1.service_case.id"),
            name="service_assignment_case_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "assignee_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="service_assignment_assignee_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "assigned_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="service_assignment_assigner_enterprise_fk",
        ),
        CheckConstraint(
            "capacity IN ('employee','consultant','partner')",
            name="service_assignment_capacity_ck",
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','revoked')",
            name="service_assignment_status_ck",
        ),
        CheckConstraint(
            "(status = 'pending' AND responded_at IS NULL AND revoked_at IS NULL) "
            "OR (status IN ('accepted','rejected') "
            "AND responded_at IS NOT NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="service_assignment_state_time_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    service_case_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    capacity: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SiteVisit(Base):
    __tablename__ = "site_visit"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="site_visit_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "service_case_id"),
            ("f1.service_case.enterprise_id", "f1.service_case.id"),
            name="site_visit_case_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="site_visit_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('planned','in_progress','completed','cancelled')",
            name="site_visit_status_ck",
        ),
        CheckConstraint(
            "planned_start_at IS NULL OR planned_end_at IS NULL "
            "OR planned_end_at >= planned_start_at",
            name="site_visit_planned_window_ck",
        ),
        CheckConstraint(
            "(status = 'planned' AND started_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'in_progress' AND started_at IS NOT NULL "
            "AND completed_at IS NULL) "
            "OR (status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at) "
            "OR (status = 'cancelled' AND completed_at IS NULL)",
            name="site_visit_state_time_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    service_case_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String, default="planned")
    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class Finding(Base):
    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="finding_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "service_case_id"),
            ("f1.service_case.enterprise_id", "f1.service_case.id"),
            name="finding_case_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "site_visit_id"),
            ("f1.site_visit.enterprise_id", "f1.site_visit.id"),
            name="finding_visit_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "responsible_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="finding_responsible_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="finding_creator_enterprise_fk",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="finding_severity_ck",
        ),
        CheckConstraint(
            "status IN ('open','rectifying','submitted','reviewing',"
            "'passed','rejected','closed')",
            name="finding_status_ck",
        ),
        CheckConstraint(
            "service_case_id IS NOT NULL OR site_visit_id IS NOT NULL",
            name="finding_context_required_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    service_case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    site_visit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, default="open")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class CorrectiveAction(Base):
    __tablename__ = "corrective_action"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="corrective_action_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "finding_id",
            "revision",
            name="corrective_action_finding_revision_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "finding_id"),
            ("f1.finding.enterprise_id", "f1.finding.id"),
            name="corrective_action_finding_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "submitted_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="corrective_action_submitter_enterprise_fk",
        ),
        CheckConstraint("revision > 0", name="corrective_action_revision_ck"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class FindingReview(Base):
    __tablename__ = "finding_review"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="finding_review_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "finding_id"),
            ("f1.finding.enterprise_id", "f1.finding.id"),
            name="finding_review_finding_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "reviewer_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="finding_review_reviewer_enterprise_fk",
        ),
        CheckConstraint(
            "decision IN ('passed','rejected')",
            name="finding_review_decision_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    decision: Mapped[str] = mapped_column(String)
    comment: Mapped[str] = mapped_column(Text)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class BusinessTimeline(Base):
    __tablename__ = "business_timeline"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="business_timeline_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "service_case_id"),
            ("f1.service_case.enterprise_id", "f1.service_case.id"),
            name="business_timeline_case_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "actor_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="business_timeline_actor_enterprise_fk",
        ),
        CheckConstraint(
            "char_length(event_type) BETWEEN 1 AND 64",
            name="business_timeline_event_type_ck",
        ),
        CheckConstraint(
            "char_length(subject_type) BETWEEN 1 AND 64",
            name="business_timeline_subject_type_ck",
        ),
        CheckConstraint(
            "status IS NULL OR char_length(status) BETWEEN 1 AND 64",
            name="business_timeline_status_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    service_case_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    event_type: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class InAppNotification(Base):
    __tablename__ = "in_app_notification"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="in_app_notification_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "recipient_user_id",
            "timeline_event_id",
            name="in_app_notification_recipient_event_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "recipient_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="in_app_notification_recipient_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "timeline_event_id"),
            ("f1.business_timeline.enterprise_id", "f1.business_timeline.id"),
            name="in_app_notification_timeline_enterprise_fk",
        ),
        CheckConstraint(
            "read_at IS NULL OR read_at >= created_at",
            name="in_app_notification_read_time_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    timeline_event_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DocumentRecord(Base):
    __tablename__ = "document_record"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="document_record_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "plant_id"),
            ("f1.plant.enterprise_id", "f1.plant.id"),
            name="document_record_plant_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="document_record_creator_enterprise_fk",
        ),
        CheckConstraint("status IN ('active','archived')", name="document_record_status_ck"),
        CheckConstraint("latest_version_no >= 0", name="document_record_latest_version_ck"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    plant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String, default="active")
    latest_version_no: Mapped[int] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="document_version_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "document_record_id",
            "version_no",
            name="document_version_record_version_uq",
        ),
        UniqueConstraint(
            "enterprise_id",
            "idempotency_key_sha256",
            name="document_version_idempotency_uq",
        ),
        UniqueConstraint(
            "enterprise_id",
            "upload_task_id",
            name="document_version_task_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "document_record_id"),
            ("f1.document_record.enterprise_id", "f1.document_record.id"),
            name="document_version_record_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "source_document_id"),
            ("f1.document.enterprise_id", "f1.document.id"),
            name="document_version_source_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "upload_task_id"),
            ("f1.upload_task.enterprise_id", "f1.upload_task.id"),
            name="document_version_task_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "upload_task_id", "source_document_id"),
            (
                "f1.upload_task.enterprise_id",
                "f1.upload_task.id",
                "f1.upload_task.document_id",
            ),
            name="document_version_task_source_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="document_version_creator_enterprise_fk",
        ),
        CheckConstraint("version_no > 0", name="document_version_number_ck"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    document_record_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_no: Mapped[int] = mapped_column(Integer)
    source_document_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    upload_task_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    display_filename: Mapped[str] = mapped_column(String(255))
    idempotency_key_sha256: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class DocumentPreviewUnit(Base):
    __tablename__ = "document_preview_unit"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="document_preview_unit_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "document_version_id",
            "ordinal",
            name="document_preview_unit_version_ordinal_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "document_version_id"),
            ("f1.document_version.enterprise_id", "f1.document_version.id"),
            name="document_preview_unit_version_enterprise_fk",
        ),
        CheckConstraint(
            "unit_kind IN ('page_text','worksheet_grid','image')",
            name="document_preview_unit_kind_ck",
        ),
        CheckConstraint(
            "content_type IN ('image/jpeg','application/json')",
            name="document_preview_unit_content_type_ck",
        ),
        CheckConstraint(
            "(unit_kind IN ('page_text','worksheet_grid') "
            "AND content_type='application/json' "
            "AND size_bytes BETWEEN 1 AND 262144) OR "
            "(unit_kind='image' AND content_type='image/jpeg' "
            "AND size_bytes BETWEEN 1 AND 20971520 "
            "AND width_px BETWEEN 1 AND 10000 "
            "AND height_px BETWEEN 1 AND 10000)",
            name="document_preview_unit_payload_ck",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("f1.enterprise.id")
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    unit_kind: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(128))
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str] = mapped_column(String)
    object_key: Mapped[str] = mapped_column(String(160))
    content_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class CrmAccount(Base):
    __tablename__ = "crm_account"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="crm_account_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "owner_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="crm_account_owner_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="crm_account_creator_enterprise_fk",
        ),
        CheckConstraint(
            "stage IN ('lead','active','dormant','closed')",
            name="crm_account_stage_ck",
        ),
        Index("crm_account_stage_idx", "enterprise_id", "stage", "updated_at"),
        Index("crm_account_follow_up_idx", "enterprise_id", "next_follow_up_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    display_name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String, default="lead")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    industry_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    region_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class CrmContact(Base):
    __tablename__ = "crm_contact"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="crm_contact_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "account_id"),
            ("f1.crm_account.enterprise_id", "f1.crm_account.id"),
            name="crm_contact_account_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="crm_contact_creator_enterprise_fk",
        ),
        CheckConstraint("status IN ('active','inactive')", name="crm_contact_status_ck"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    display_name: Mapped[str] = mapped_column(String(200))
    role_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class CrmFollowUp(Base):
    __tablename__ = "crm_follow_up"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="crm_follow_up_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "account_id"),
            ("f1.crm_account.enterprise_id", "f1.crm_account.id"),
            name="crm_follow_up_account_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "actor_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="crm_follow_up_actor_enterprise_fk",
        ),
        CheckConstraint(
            "channel IN ('onsite','meeting','phone','internal_note')",
            name="crm_follow_up_channel_ck",
        ),
        Index("crm_follow_up_account_idx", "enterprise_id", "account_id", "occurred_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    channel: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class BusinessReport(Base):
    __tablename__ = "business_report"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="business_report_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "service_case_id"),
            ("f1.service_case.enterprise_id", "f1.service_case.id"),
            name="business_report_case_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="business_report_creator_enterprise_fk",
        ),
        CheckConstraint("status IN ('active','archived')", name="business_report_status_ck"),
        CheckConstraint("current_version_no >= 0", name="business_report_version_no_ck"),
        Index("business_report_case_idx", "enterprise_id", "service_case_id", "updated_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    service_case_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String, default="active")
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class BusinessReportVersion(Base):
    __tablename__ = "business_report_version"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="business_report_version_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "report_id",
            "version_number",
            name="business_report_version_number_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "report_id"),
            ("f1.business_report.enterprise_id", "f1.business_report.id"),
            name="business_report_version_report_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="business_report_version_creator_enterprise_fk",
        ),
        CheckConstraint("version_number > 0", name="business_report_version_number_ck"),
        CheckConstraint(
            "lifecycle IN ('current','superseded','void')",
            name="business_report_version_lifecycle_ck",
        ),
        CheckConstraint(
            "snapshot_size_bytes BETWEEN 2 AND 4194304",
            name="business_report_version_size_ck",
        ),
        Index(
            "business_report_version_current_uq",
            "enterprise_id",
            "report_id",
            unique=True,
            postgresql_where=text("lifecycle = 'current'"),
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    report_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_number: Mapped[int] = mapped_column(Integer)
    lifecycle: Mapped[str] = mapped_column(String, default="current")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_size_bytes: Mapped[int] = mapped_column(BigInteger)
    source_counts: Mapped[dict[str, int]] = mapped_column(JSON)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class BusinessReportArtifact(Base):
    __tablename__ = "business_report_artifact"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="business_report_artifact_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "report_version_id",
            name="business_report_artifact_version_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "report_version_id"),
            ("f1.business_report_version.enterprise_id", "f1.business_report_version.id"),
            name="business_report_artifact_version_enterprise_fk",
        ),
        CheckConstraint("artifact_kind = 'canonical_json'", name="business_report_artifact_kind_ck"),
        CheckConstraint(
            "storage_kind = 'database_snapshot'", name="business_report_artifact_storage_ck"
        ),
        CheckConstraint(
            "content_type = 'application/json'", name="business_report_artifact_content_ck"
        ),
        CheckConstraint("status = 'ready'", name="business_report_artifact_status_ck"),
        CheckConstraint(
            "size_bytes BETWEEN 2 AND 4194304", name="business_report_artifact_size_ck"
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    report_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    artifact_kind: Mapped[str] = mapped_column(String, default="canonical_json")
    storage_kind: Mapped[str] = mapped_column(String, default="database_snapshot")
    content_type: Mapped[str] = mapped_column(String, default="application/json")
    status: Mapped[str] = mapped_column(String, default="ready")
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class PolicySource(Base):
    __tablename__ = "policy_source"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="policy_source_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_source_creator_enterprise_fk",
        ),
        CheckConstraint(
            "source_type IN ('law','regulation','standard','guidance','internal')",
            name="policy_source_type_ck",
        ),
        CheckConstraint("status IN ('active','archived')", name="policy_source_status_ck"),
        Index("policy_source_search_idx", "enterprise_id", "status", "source_type"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    title: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String)
    jurisdiction: Mapped[str] = mapped_column(String(120))
    source_reference: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String, default="active")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class PolicyVersion(Base):
    __tablename__ = "policy_version"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="policy_version_enterprise_id_id_uq"),
        UniqueConstraint(
            "enterprise_id", "source_id", "version_number", name="policy_version_source_number_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "source_id"),
            ("f1.policy_source.enterprise_id", "f1.policy_source.id"),
            name="policy_version_source_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "document_version_id"),
            ("f1.document_version.enterprise_id", "f1.document_version.id"),
            name="policy_version_document_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_version_creator_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "submitted_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_version_submitter_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "approved_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_version_approver_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "published_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_version_publisher_enterprise_fk",
        ),
        CheckConstraint(
            "domain IN ('safety','health','environment','fire','chemical','general')",
            name="policy_version_domain_ck",
        ),
        CheckConstraint(
            "effect_status IN ('unknown','not_effective','effective','expired')",
            name="policy_version_effect_status_ck",
        ),
        CheckConstraint(
            "workflow_status IN ('draft','in_review','approved','rejected','published','superseded')",
            name="policy_version_workflow_status_ck",
        ),
        Index(
            "policy_version_published_uq",
            "enterprise_id",
            "source_id",
            unique=True,
            postgresql_where=text("workflow_status = 'published'"),
        ),
        Index(
            "policy_version_search_idx",
            "enterprise_id",
            "domain",
            "effect_status",
            "workflow_status",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String)
    effect_status: Mapped[str] = mapped_column(String, default="unknown")
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workflow_status: Mapped[str] = mapped_column(String, default="draft")
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class PolicyReviewEvent(Base):
    __tablename__ = "policy_review_event"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="policy_review_event_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "policy_version_id"),
            ("f1.policy_version.enterprise_id", "f1.policy_version.id"),
            name="policy_review_event_version_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "actor_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_review_event_actor_enterprise_fk",
        ),
        CheckConstraint(
            "action IN ('submitted','approved','rejected','published')",
            name="policy_review_event_action_ck",
        ),
        Index(
            "policy_review_event_version_idx",
            "enterprise_id",
            "policy_version_id",
            "occurred_at",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    policy_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class PolicyImpactCandidate(Base):
    __tablename__ = "policy_impact_candidate"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="policy_impact_candidate_enterprise_id_id_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "policy_version_id"),
            ("f1.policy_version.enterprise_id", "f1.policy_version.id"),
            name="policy_impact_candidate_version_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_impact_candidate_creator_enterprise_fk",
        ),
        CheckConstraint(
            "domain IN ('safety','health','environment','fire','chemical','general')",
            name="policy_impact_candidate_domain_ck",
        ),
        CheckConstraint(
            "priority IN ('low','medium','high','critical')",
            name="policy_impact_candidate_priority_ck",
        ),
        CheckConstraint(
            "status IN ('open','accepted','dismissed')",
            name="policy_impact_candidate_status_ck",
        ),
        Index(
            "policy_impact_candidate_status_idx",
            "enterprise_id",
            "status",
            "priority",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    policy_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    domain: Mapped[str] = mapped_column(String)
    scope_note: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="open")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class PolicyImpactTask(Base):
    __tablename__ = "policy_impact_task"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="policy_impact_task_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "impact_candidate_id"),
            ("f1.policy_impact_candidate.enterprise_id", "f1.policy_impact_candidate.id"),
            name="policy_impact_task_candidate_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "owner_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_impact_task_owner_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="policy_impact_task_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('open','in_progress','completed','dismissed')",
            name="policy_impact_task_status_ck",
        ),
        Index(
            "policy_impact_task_owner_idx",
            "enterprise_id",
            "owner_user_id",
            "status",
            "due_at",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    impact_candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(300))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class QualitySuite(Base):
    __tablename__ = "quality_suite"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="quality_suite_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="quality_suite_creator_enterprise_fk",
        ),
        CheckConstraint(
            "category IN ('ingestion','retrieval','qa','authorization','injection')",
            name="quality_suite_category_ck",
        ),
        CheckConstraint("status IN ('active','archived')", name="quality_suite_status_ck"),
        Index("quality_suite_status_idx", "enterprise_id", "status", "updated_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class QualityScenario(Base):
    __tablename__ = "quality_scenario"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="quality_scenario_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "suite_id",
            "scenario_key",
            name="quality_scenario_suite_key_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "suite_id"),
            ("f1.quality_suite.enterprise_id", "f1.quality_suite.id"),
            name="quality_scenario_suite_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="quality_scenario_creator_enterprise_fk",
        ),
        CheckConstraint(
            "scenario_type IN ('exact_match','threshold','refusal_required',"
            "'isolation_required','injection_blocked','disagreement_max')",
            name="quality_scenario_type_ck",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="quality_scenario_severity_ck",
        ),
        Index(
            "quality_scenario_suite_idx",
            "enterprise_id",
            "suite_id",
            "enabled",
            "scenario_key",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    scenario_key: Mapped[str] = mapped_column(String(80))
    scenario_type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    oracle_config: Mapped[dict[str, object]] = mapped_column(JSON)
    synthetic_observation: Mapped[dict[str, object]] = mapped_column(JSON)
    scenario_sha256: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class QualityRun(Base):
    __tablename__ = "quality_run"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="quality_run_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "suite_id"),
            ("f1.quality_suite.enterprise_id", "f1.quality_suite.id"),
            name="quality_run_suite_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="quality_run_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('queued','running','passed','failed','cancelled')",
            name="quality_run_status_ck",
        ),
        CheckConstraint("trigger_kind = 'manual'", name="quality_run_trigger_kind_ck"),
        CheckConstraint(
            "passed_count + failed_count + error_count <= total_count",
            name="quality_run_counts_ck",
        ),
        Index("quality_run_suite_idx", "enterprise_id", "suite_id", "created_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String, default="queued")
    trigger_kind: Mapped[str] = mapped_column(String, default="manual")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QualityResult(Base):
    __tablename__ = "quality_result"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="quality_result_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "run_id",
            "scenario_id",
            name="quality_result_run_scenario_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "run_id"),
            ("f1.quality_run.enterprise_id", "f1.quality_run.id"),
            name="quality_result_run_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "scenario_id"),
            ("f1.quality_scenario.enterprise_id", "f1.quality_scenario.id"),
            name="quality_result_scenario_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('passed','failed','error')", name="quality_result_status_ck"
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    scenario_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String)
    reason_code: Mapped[str] = mapped_column(String(80))
    observed_metrics: Mapped[dict[str, object]] = mapped_column(JSON)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class QualityDisagreement(Base):
    __tablename__ = "quality_disagreement"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="quality_disagreement_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id", "result_id", name="quality_disagreement_result_uq"
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "result_id"),
            ("f1.quality_result.enterprise_id", "f1.quality_result.id"),
            name="quality_disagreement_result_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "reviewed_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="quality_disagreement_reviewer_enterprise_fk",
        ),
        CheckConstraint(
            "kind IN ('parser','ocr','citation','refusal','authorization','injection')",
            name="quality_disagreement_kind_ck",
        ),
        CheckConstraint(
            "review_status IN ('open','acknowledged','waived')",
            name="quality_disagreement_review_status_ck",
        ),
        Index(
            "quality_disagreement_review_idx",
            "enterprise_id",
            "review_status",
            "created_at",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String)
    left_digest: Mapped[str] = mapped_column(String(64))
    right_digest: Mapped[str] = mapped_column(String(64))
    score: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    review_status: Mapped[str] = mapped_column(String, default="open")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class RehearsalPlan(Base):
    __tablename__ = "rehearsal_plan"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="rehearsal_plan_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="rehearsal_plan_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('draft','active','archived')", name="rehearsal_plan_status_ck"
        ),
        CheckConstraint(
            "execution_mode = 'local_manual'", name="rehearsal_plan_execution_mode_ck"
        ),
        Index("rehearsal_plan_status_idx", "enterprise_id", "status", "updated_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String, default="active")
    execution_mode: Mapped[str] = mapped_column(String, default="local_manual")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class RehearsalCheck(Base):
    __tablename__ = "rehearsal_check"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="rehearsal_check_enterprise_id_id_uq"),
        UniqueConstraint(
            "enterprise_id", "plan_id", "check_key", name="rehearsal_check_plan_key_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "plan_id",
            "sequence_no",
            name="rehearsal_check_plan_sequence_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "plan_id"),
            ("f1.rehearsal_plan.enterprise_id", "f1.rehearsal_plan.id"),
            name="rehearsal_check_plan_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="rehearsal_check_creator_enterprise_fk",
        ),
        CheckConstraint(
            "category IN ('service','dependency','backup','restore','security','rollback')",
            name="rehearsal_check_category_ck",
        ),
        Index(
            "rehearsal_check_plan_idx",
            "enterprise_id",
            "plan_id",
            "enabled",
            "sequence_no",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    check_key: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String(200))
    sequence_no: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


class RehearsalRun(Base):
    __tablename__ = "rehearsal_run"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "id", name="rehearsal_run_enterprise_id_id_uq"),
        ForeignKeyConstraint(
            ("enterprise_id", "plan_id"),
            ("f1.rehearsal_plan.enterprise_id", "f1.rehearsal_plan.id"),
            name="rehearsal_run_plan_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "created_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="rehearsal_run_creator_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('planned','running','passed','failed','cancelled')",
            name="rehearsal_run_status_ck",
        ),
        CheckConstraint(
            "passed_count + failed_count + blocked_count + pending_count = total_count",
            name="rehearsal_run_counts_ck",
        ),
        Index("rehearsal_run_plan_idx", "enterprise_id", "plan_id", "created_at"),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String, default="planned")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    rollback_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RehearsalCheckResult(Base):
    __tablename__ = "rehearsal_check_result"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "id", name="rehearsal_check_result_enterprise_id_id_uq"
        ),
        UniqueConstraint(
            "enterprise_id",
            "run_id",
            "check_id",
            name="rehearsal_check_result_run_check_uq",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "run_id"),
            ("f1.rehearsal_run.enterprise_id", "f1.rehearsal_run.id"),
            name="rehearsal_check_result_run_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "check_id"),
            ("f1.rehearsal_check.enterprise_id", "f1.rehearsal_check.id"),
            name="rehearsal_check_result_check_enterprise_fk",
        ),
        ForeignKeyConstraint(
            ("enterprise_id", "recorded_by_user_id"),
            ("f1.enterprise_user.enterprise_id", "f1.enterprise_user.user_id"),
            name="rehearsal_check_result_recorder_enterprise_fk",
        ),
        CheckConstraint(
            "status IN ('pending','passed','failed','blocked')",
            name="rehearsal_check_result_status_ck",
        ),
        Index(
            "rehearsal_result_run_idx",
            "enterprise_id",
            "run_id",
            "sequence_no",
        ),
        {"schema": "f1"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enterprise_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("f1.enterprise.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    check_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    check_key: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String(200))
    sequence_no: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String, default="pending")
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp()
    )


__all__ = (
    "Enterprise",
    "Plant",
    "UserProfile",
    "EnterpriseUser",
    "Document",
    "AuditLog",
    "UploadTask",
    "Outbox",
    "QaRequest",
    "InviteJti",
    "ServiceCase",
    "ServiceAssignment",
    "SiteVisit",
    "Finding",
    "CorrectiveAction",
    "FindingReview",
    "BusinessTimeline",
    "InAppNotification",
    "DocumentRecord",
    "DocumentVersion",
    "DocumentPreviewUnit",
    "CrmAccount",
    "CrmContact",
    "CrmFollowUp",
    "BusinessReport",
    "BusinessReportVersion",
    "BusinessReportArtifact",
    "PolicySource",
    "PolicyVersion",
    "PolicyReviewEvent",
    "PolicyImpactCandidate",
    "PolicyImpactTask",
    "QualitySuite",
    "QualityScenario",
    "QualityRun",
    "QualityResult",
    "QualityDisagreement",
    "RehearsalPlan",
    "RehearsalCheck",
    "RehearsalRun",
    "RehearsalCheckResult",
    "ROLES",
    "DOC_STATUSES",
    "TASK_STATUSES",
    "SERVICE_CASE_STATUSES",
    "SERVICE_ASSIGNMENT_CAPACITIES",
    "SERVICE_ASSIGNMENT_STATUSES",
    "SITE_VISIT_STATUSES",
    "FINDING_SEVERITIES",
    "FINDING_STATUSES",
    "FINDING_REVIEW_DECISIONS",
    "INGESTION_PIPELINES",
    "INGESTION_STAGES",
    "INGESTION_SCAN_VERDICTS",
    "INGESTION_PREVIEW_STATUSES",
    "CRM_ACCOUNT_STAGES",
    "CRM_CONTACT_STATUSES",
    "CRM_FOLLOW_UP_CHANNELS",
    "BUSINESS_REPORT_STATUSES",
    "BUSINESS_REPORT_VERSION_LIFECYCLES",
    "POLICY_SOURCE_TYPES",
    "POLICY_DOMAINS",
    "POLICY_EFFECT_STATUSES",
    "POLICY_WORKFLOW_STATUSES",
    "POLICY_IMPACT_PRIORITIES",
    "QUALITY_SUITE_CATEGORIES",
    "QUALITY_SCENARIO_TYPES",
    "QUALITY_SEVERITIES",
    "QUALITY_RUN_STATUSES",
    "QUALITY_RESULT_STATUSES",
    "QUALITY_DISAGREEMENT_KINDS",
    "QUALITY_DISAGREEMENT_REVIEW_STATUSES",
    "REHEARSAL_PLAN_STATUSES",
    "REHEARSAL_CHECK_CATEGORIES",
    "REHEARSAL_RUN_STATUSES",
    "REHEARSAL_RESULT_STATUSES",
)
