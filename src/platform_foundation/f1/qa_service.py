"""Tenant-scoped QA reservation, execution, and encrypted completion.

The request id is reserved and bound to the authenticated enterprise and the
question digest *before* retrieval or LLM work starts.  Exactly one lease owner
may perform external work.  A terminal transition is an owner-token compare
and swap, and the matching audit row is written in the same transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sqlalchemy import text

from .auth import Tenant
from .database import session_scope

# Tests may replace this with a temporary 0600 file.  Runtime resolves only an
# explicit file or the configured secret directory; there is no host default.
QA_KEY_FILE: Path | None = None
QA_OWNER_LEASE_SECONDS = 300
_CIPHERTEXT_MAGIC = b"F1Q1"


class QaResult:
    def __init__(
        self,
        answer: str | None,
        citations: list[dict],
        refusal_reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.answer = answer
        self.citations = citations
        self.refusal_reason = refusal_reason
        self.request_id = request_id

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "citations": self.citations,
            "refusal_reason": self.refusal_reason,
            "request_id": self.request_id,
        }


class ReservationState(str, Enum):
    CLAIMED = "CLAIMED"
    REPLAY = "REPLAY"
    IN_PROGRESS = "IN_PROGRESS"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class QaReservation:
    state: ReservationState
    request_id: uuid.UUID
    owner_token: uuid.UUID | None = None
    result: QaResult | None = None
    attempt: int = 0


class RequestIdConflict(RuntimeError):
    """A request id was reused with a different enterprise or question."""


class RequestInProgress(RuntimeError):
    """The same request is currently owned by another unexpired claimant."""


class RequestOwnershipLost(RuntimeError):
    """The claimant no longer owns the request completion lease."""


class QaOutcomeInvalid(RuntimeError):
    """The chain returned a state that cannot satisfy the persistence contract."""


def _qa_key_path() -> Path:
    if QA_KEY_FILE is not None:
        path = QA_KEY_FILE
    else:
        raw_path = os.environ.get("F1_QA_KEY_FILE", "").strip()
        if not raw_path:
            raw_dir = os.environ.get("F1_SECRETS_DIR", "").strip()
            if raw_dir:
                raw_path = str(Path(raw_dir) / "f1_qa_key")
        if not raw_path:
            raise RuntimeError("F1_QA_KEY_UNAVAILABLE")
        path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("F1_QA_KEY_INVALID")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        raise RuntimeError("F1_QA_KEY_PERMISSIONS")
    return path


def _key_bytes() -> bytes:
    raw = _qa_key_path().read_text(encoding="ascii").strip()
    return bytes.fromhex(raw) if len(raw) == 64 else raw.encode("utf-8")


def _question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def _aad(request_id: uuid.UUID, enterprise_id: uuid.UUID, question_sha256: str) -> bytes:
    """Return stable, body-free AAD binding the three replay identities."""
    return (
        "f1.qa.response.v1\x00"
        f"{request_id}\x00{enterprise_id}\x00{question_sha256}"
    ).encode("ascii")


def _canonical_payload(outcome: QaResult) -> str:
    return json.dumps(
        {"answer": outcome.answer, "citations": outcome.citations},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_outcome(outcome: QaResult) -> str:
    has_answer = outcome.answer is not None
    has_refusal = bool(outcome.refusal_reason)
    if has_answer == has_refusal:
        raise QaOutcomeInvalid("QA_OUTCOME_STATE_INVALID")
    if has_answer and not outcome.citations:
        raise QaOutcomeInvalid("QA_CITATIONS_REQUIRED")
    if has_refusal and outcome.citations:
        raise QaOutcomeInvalid("QA_REFUSAL_CITATIONS_FORBIDDEN")
    return "done" if has_answer else "refused"


def _result_from_row(
    row: object,
    request_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    question_sha256: str,
) -> QaResult | None:
    """Decode a terminal row selected as
    ``status, refusal_reason, ciphertext, attempt``.

    Rows completed before f1_0004 have ``attempt=0`` and use the legacy
    no-AAD envelope.  All claims created by the repaired flow start at attempt
    one and are required to carry the versioned AAD envelope.
    """
    status, refusal_reason, ciphertext, attempt = row  # type: ignore[misc]
    if status == "accepted":
        return None
    if status == "refused":
        return QaResult(None, [], refusal_reason, str(request_id))
    if status != "done" or ciphertext is None:
        return None
    aad = _aad(request_id, enterprise_id, question_sha256)
    payload = _decrypt(bytes(ciphertext), aad, allow_legacy=int(attempt or 0) == 0)
    data = json.loads(payload)
    return QaResult(
        answer=data.get("answer"),
        citations=data.get("citations", []),
        request_id=str(request_id),
    )


async def lookup_request(
    request_id: uuid.UUID, tenant: Tenant, question: str | None = None
) -> QaResult | None:
    """Read a terminal replay without claiming a missing or expired request."""
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, refusal_reason, response_encrypted, "
                    "enterprise_id, question_sha256, attempt "
                    "FROM f1.qa_request WHERE request_id = :rid"
                ),
                {"rid": request_id},
            )
        ).fetchone()
        if row is None:
            return None
        qsha = _question_sha256(question) if question is not None else str(row[4])
        if row[3] != tenant.enterprise_id or str(row[4]) != qsha:
            raise RequestIdConflict("REQUEST_ID_CONFLICT")
        return _result_from_row(
            (row[0], row[1], row[2], row[5]), request_id, tenant.enterprise_id, qsha
        )


async def reserve_request(
    request_id: uuid.UUID,
    tenant: Tenant,
    question: str,
    *,
    lease_seconds: int = QA_OWNER_LEASE_SECONDS,
) -> QaReservation:
    """Atomically reserve the request before any external call.

    The SECURITY DEFINER function owns the insert/row-lock/reclaim transaction;
    the API role has no direct write privilege on ``qa_request``.
    """
    if lease_seconds <= 0:
        raise ValueError("QA_LEASE_INVALID")
    qsha = _question_sha256(question)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT claim_state, owner_token, attempt, status, "
                    "refusal_reason, response_encrypted, response_sha256 "
                    "FROM f1.claim_qa_request(:rid, :qsha, :lease_seconds)"
                ),
                {
                    "rid": request_id,
                    "qsha": qsha,
                    "lease_seconds": lease_seconds,
                },
            )
        ).fetchone()
        if row is None:
            raise QaOutcomeInvalid("QA_CLAIM_RESULT_INVALID")
        try:
            state = ReservationState(str(row[0]))
        except ValueError:
            raise QaOutcomeInvalid("QA_CLAIM_STATE_INVALID") from None
        attempt = int(row[2] or 0)
        owner_token = row[1]
        if state is ReservationState.REPLAY:
            replay = _result_from_row(
                (row[3], row[4], row[5], attempt),
                request_id,
                tenant.enterprise_id,
                qsha,
            )
            if replay is None:
                raise QaOutcomeInvalid("QA_REPLAY_INVALID")
            return QaReservation(
                state, request_id, result=replay, attempt=attempt
            )
        if state is ReservationState.CLAIMED:
            if owner_token is None:
                raise QaOutcomeInvalid("QA_CLAIM_OWNER_MISSING")
            await session.commit()
            return QaReservation(
                state,
                request_id,
                owner_token=uuid.UUID(str(owner_token)),
                attempt=attempt,
            )
        return QaReservation(state, request_id, attempt=attempt)


async def ask_question(
    question: str,
    request_id: uuid.UUID,
    tenant: Tenant,
) -> QaResult:
    """Claim, run the external chain only as owner, then owner-CAS complete."""
    reservation = await reserve_request(request_id, tenant, question)
    if reservation.state is ReservationState.CONFLICT:
        raise RequestIdConflict("REQUEST_ID_CONFLICT")
    if reservation.state is ReservationState.IN_PROGRESS:
        raise RequestInProgress("REQUEST_IN_PROGRESS")
    if reservation.state is ReservationState.REPLAY:
        if reservation.result is None:
            raise QaOutcomeInvalid("QA_REPLAY_INVALID")
        return reservation.result
    if reservation.state is not ReservationState.CLAIMED:
        raise RequestOwnershipLost("REQUEST_RESERVATION_INVALID")
    if reservation.owner_token is None:
        raise RequestOwnershipLost("REQUEST_OWNER_MISSING")

    from . import qa_chain

    outcome = await qa_chain.run(question, tenant)
    outcome.request_id = str(request_id)
    await complete_request(request_id, tenant, question, reservation.owner_token, outcome)
    return outcome


async def complete_request(
    request_id: uuid.UUID,
    tenant: Tenant,
    question: str,
    owner_token: uuid.UUID,
    outcome: QaResult,
) -> None:
    """Owner-token CAS the terminal state and audit it in one transaction."""
    terminal = _validate_outcome(outcome)
    qsha = _question_sha256(question)
    encrypted: bytes | None = None
    response_sha256: str | None = None
    refusal_reason: str | None = None
    if terminal == "done":
        stored = _canonical_payload(outcome)
        encrypted = _encrypt(stored, _aad(request_id, tenant.enterprise_id, qsha))
        response_sha256 = hashlib.sha256(stored.encode("utf-8")).hexdigest()
    else:
        refusal_reason = outcome.refusal_reason

    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        completed = bool(
            (
                await session.execute(
                text(
                    "SELECT f1.complete_qa_request("
                    ":rid, :owner, :qsha, :status, :enc, :rsha, :reason)"
                ),
                {
                    "status": terminal,
                    "enc": encrypted,
                    "rsha": response_sha256,
                    "reason": refusal_reason,
                    "rid": request_id,
                    "qsha": qsha,
                    "owner": owner_token,
                },
                )
            ).scalar()
        )
        if not completed:
            raise RequestOwnershipLost("REQUEST_OWNERSHIP_LOST")
        await session.commit()


def _encrypt(plaintext: str, aad: bytes) -> bytes:
    """Encrypt a response with AES-GCM and versioned request-bound AAD."""
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _key_bytes()
    if len(key) not in (16, 24, 32):
        raise RuntimeError("F1_QA_KEY_INVALID_LENGTH")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return _CIPHERTEXT_MAGIC + nonce + ciphertext


def _decrypt(ciphertext: bytes, aad: bytes, *, allow_legacy: bool = False) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _key_bytes()
    if ciphertext.startswith(_CIPHERTEXT_MAGIC):
        payload = ciphertext[len(_CIPHERTEXT_MAGIC) :]
        nonce, encrypted = payload[:12], payload[12:]
        plaintext = AESGCM(key).decrypt(nonce, encrypted, aad)
    elif allow_legacy:
        nonce, encrypted = ciphertext[:12], ciphertext[12:]
        plaintext = AESGCM(key).decrypt(nonce, encrypted, None)
    else:
        raise ValueError("F1_QA_CIPHERTEXT_VERSION_INVALID")
    return plaintext.decode("utf-8")


__all__ = (
    "QaResult",
    "QaReservation",
    "ReservationState",
    "lookup_request",
    "reserve_request",
    "ask_question",
    "complete_request",
    "RequestIdConflict",
    "RequestInProgress",
    "RequestOwnershipLost",
    "QaOutcomeInvalid",
)
