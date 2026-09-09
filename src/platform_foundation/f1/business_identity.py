"""Organization-aware identity projection shared by business entry points.

This projection identifies the business audience. Individual professional
commands still enforce their action and object permissions independently.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from .auth import Tenant

if TYPE_CHECKING:
    from .features.analysis_reports.contracts import ProductRole


def product_role_for(tenant: Tenant) -> ProductRole:
    membership = tenant.role or ""
    if tenant.business_kind is None:
        # Compatibility for the default engineering schema (before f1_0030).
        return "provider_admin" if membership in {"super_admin", "enterprise_admin"} else "client_user"
    if membership == "super_admin":
        return "technical_admin"
    if tenant.business_kind == "service_provider":
        return {
            "enterprise_admin": "provider_admin",
            "plant_admin": "provider_consultant",
            "auditor": "provider_reviewer",
        }.get(membership, "unconfigured")
    if tenant.business_kind == "client" and membership in {"enterprise_admin", "plant_admin", "partner", "auditor"}:
        return "client_user"
    return "unconfigured"

