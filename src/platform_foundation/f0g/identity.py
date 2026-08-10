"""Stable opaque Fixture identities backed by an independent token bundle."""

from __future__ import annotations

import uuid

from ..f0e.hashing import stable_uuid4
from .contracts import FixtureActorSession
from .tokens import FixtureTokenBundle, load_token_bundle


_ROLES = ("ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR")


def fixture_actor_sessions(
    enterprise_id: uuid.UUID, tokens: FixtureTokenBundle
) -> tuple[FixtureActorSession, ...]:
    return tuple(
        FixtureActorSession(
            role=role,
            actor_id=stable_uuid4("f0g-fixture-actor-v1", enterprise_id, role),
            session_id=stable_uuid4("f0g-fixture-session-v1", enterprise_id, role),
            token=tokens.token(role),
        )
        for role in _ROLES
    )


def load_fixture_actor_sessions(
    enterprise_id: uuid.UUID, token_bundle_path: str
) -> tuple[FixtureActorSession, ...]:
    with load_token_bundle(token_bundle_path) as tokens:
        return fixture_actor_sessions(enterprise_id, tokens)


__all__ = ("fixture_actor_sessions", "load_fixture_actor_sessions")
