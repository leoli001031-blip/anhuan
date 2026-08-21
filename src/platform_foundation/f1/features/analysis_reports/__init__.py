"""Independent analysis-reports domain."""

from .contracts import (
    GenerationDisabled,
    ReportNotFound,
    ReportTransitionInvalid,
    RequestIdConflict,
    TEMPLATE_ID,
    TEMPLATE_TITLE,
)
from .service import (
    apply_transition,
    create_report,
    generate_report,
    generation_enabled,
    get_published,
    job_status,
    list_client_reports,
    list_published,
    product_role_for,
    session_access,
    version_detail,
    version_history,
)

__all__ = (
    "GenerationDisabled",
    "ReportNotFound",
    "ReportTransitionInvalid",
    "RequestIdConflict",
    "TEMPLATE_ID",
    "TEMPLATE_TITLE",
    "apply_transition",
    "create_report",
    "generate_report",
    "generation_enabled",
    "get_published",
    "job_status",
    "list_client_reports",
    "list_published",
    "product_role_for",
    "session_access",
    "version_detail",
    "version_history",
)
