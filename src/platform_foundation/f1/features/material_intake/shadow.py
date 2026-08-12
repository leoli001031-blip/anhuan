"""Default-off vendor-neutral shadow parser contract.

No vendor package is imported here.  A future isolated runner may implement
this protocol after its supply-chain gate; the API process always reports OFF.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShadowParserCapability:
    enabled: bool = False
    state: str = "disabled"
    reason_code: str = "MATERIAL_SHADOW_RUNTIME_DISABLED"


def shadow_capability() -> ShadowParserCapability:
    return ShadowParserCapability()


__all__ = ("ShadowParserCapability", "shadow_capability")
