"""Redis carries only fenced PostgreSQL job identifiers."""
import uuid

from rq import Retry

from ...upload_task import QUEUE_NAME
from ..material_pipeline.queue import _enqueue


def enqueue(job_id: uuid.UUID, token: uuid.UUID) -> None:
    if type(job_id) is not uuid.UUID or type(token) is not uuid.UUID:
        raise ValueError("NATIVE_QUEUE_IDENTITY_INVALID")
    from .worker import run_native_extraction

    _enqueue(queue_name=QUEUE_NAME, stable_id=f"f1-native-evidence-{job_id}-{token}",
        function=run_native_extraction, args=(str(job_id), str(token)),
        timeout=240, retry=Retry(max=1, interval=[5]))
