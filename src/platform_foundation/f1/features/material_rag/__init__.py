"""Material-scope RAG without legacy enterprise-dataset fallback."""

from .contracts import (
    CanonicalUnit,
    DemoUnitManifestProof,
    MaterialEvidence,
    MaterialExtractiveAnswer,
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
    if name == "uat_local":
        import importlib

        return importlib.import_module(".uat_local", __name__)
    if name in {
        "derive_audience_retrieval_context",
        "derive_retrieval_context",
        "retrieve_authorized_demo_fragment",
        "run_extractive_answer",
        "run_verified_retrieval",
        "verify_remote_candidates",
    }:
        from .service import (
            derive_audience_retrieval_context,
            derive_retrieval_context,
            retrieve_authorized_demo_fragment,
            run_extractive_answer,
            run_verified_retrieval,
            verify_remote_candidates,
        )

        return {
            "derive_audience_retrieval_context": derive_audience_retrieval_context,
            "derive_retrieval_context": derive_retrieval_context,
            "retrieve_authorized_demo_fragment": retrieve_authorized_demo_fragment,
            "run_extractive_answer": run_extractive_answer,
            "run_verified_retrieval": run_verified_retrieval,
            "verify_remote_candidates": verify_remote_candidates,
        }[name]
    raise AttributeError(name)


__all__ = (
    "CanonicalUnit",
    "DemoUnitManifestProof",
    "MaterialEvidence",
    "MaterialExtractiveAnswer",
    "MaterialRagContextNotFound",
    "MaterialRagForbidden",
    "MaterialRagIntegrityError",
    "MaterialRagUnavailable",
    "MaterialRetrievalResult",
    "RetrievalContext",
    "derive_audience_retrieval_context",
    "derive_retrieval_context",
    "retrieve_authorized_demo_fragment",
    "run_extractive_answer",
    "run_verified_retrieval",
    "verify_remote_candidates",
)
