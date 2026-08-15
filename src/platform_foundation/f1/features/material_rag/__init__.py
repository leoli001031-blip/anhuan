"""Material-scope RAG without legacy enterprise-dataset fallback."""

from .contracts import (
    CanonicalUnit,
    DemoUnitManifestProof,
    MaterialEvidence,
    MaterialRagContextNotFound,
    MaterialRagForbidden,
    MaterialRagIntegrityError,
    MaterialRagUnavailable,
    MaterialRetrievalResult,
    RetrievalContext,
)


def __getattr__(name: str):
    # Avoid importing database/network adapters when callers only need the
    # value contracts (including migration and parser verification code).
    if name in {
        "derive_retrieval_context",
        "retrieve_authorized_demo_fragment",
        "run_verified_retrieval",
        "verify_remote_candidates",
    }:
        from .service import (
            derive_retrieval_context,
            retrieve_authorized_demo_fragment,
            run_verified_retrieval,
            verify_remote_candidates,
        )

        return {
            "derive_retrieval_context": derive_retrieval_context,
            "retrieve_authorized_demo_fragment": retrieve_authorized_demo_fragment,
            "run_verified_retrieval": run_verified_retrieval,
            "verify_remote_candidates": verify_remote_candidates,
        }[name]
    raise AttributeError(name)


__all__ = (
    "CanonicalUnit",
    "DemoUnitManifestProof",
    "MaterialEvidence",
    "MaterialRagContextNotFound",
    "MaterialRagForbidden",
    "MaterialRagIntegrityError",
    "MaterialRagUnavailable",
    "MaterialRetrievalResult",
    "RetrievalContext",
    "derive_retrieval_context",
    "retrieve_authorized_demo_fragment",
    "run_verified_retrieval",
    "verify_remote_candidates",
)
