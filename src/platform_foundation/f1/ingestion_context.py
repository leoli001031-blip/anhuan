"""Process-local routing to the database-enforced ingestion capability."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import uuid


@dataclass(frozen=True)
class IngestionCapability:
    delivery_id: uuid.UUID
    dispatch_token: uuid.UUID


_current: ContextVar[IngestionCapability | None] = ContextVar('ingestion_capability', default=None)


def restricted_ingestion() -> bool:
    return os.environ.get('F1_INGESTION_WORKER_RESTRICTED') == '1'


def current_capability() -> IngestionCapability | None:
    return _current.get()


@contextmanager
def ingestion_capability(delivery_id: uuid.UUID, dispatch_token: uuid.UUID):
    token = _current.set(IngestionCapability(delivery_id, dispatch_token))
    try:
        yield
    finally:
        _current.reset(token)
