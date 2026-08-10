"""F1 observability: OpenTelemetry SDK wiring.

Traces/metrics are exported to the local OTel collector (4317 gRPC) which
forwards to Jaeger and Prometheus.  Manual spans wrap QA, upload, and
retrieval operations.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# 127.0.0.1:4317 is Jaeger's host-bound OTLP receiver (the OTel collector
# receiver is internal-only per the F1 port matrix).
import os as _os
OTEL_ENDPOINT = _os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "127.0.0.1:4317")
SERVICE_NAME = "anhuan-f1-api"

_tracer: trace.Tracer | None = None
_initialized = False


def init_telemetry() -> trace.Tracer:
    """Initialize the OTel SDK once; returns the service tracer."""
    global _tracer, _initialized
    if _initialized and _tracer is not None:
        return _tracer
    if _os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        _tracer = trace.get_tracer(SERVICE_NAME)
        _initialized = True
        return _tracer
    # The local OTLP export must not go through the host HTTP proxy
    # (it would try 127.0.0.1:<proxy-port> instead of the collector).
    import os

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[key] = ""
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME})
    )
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(SERVICE_NAME)
    _initialized = True
    return _tracer


def get_tracer() -> trace.Tracer:
    return init_telemetry()


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    with get_tracer().start_as_current_span(name, attributes=attributes or {}) as span:
        yield span


def traced(name: str):
    """Decorator: wrap an async/sync function in a named span."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(name):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(name):
                return func(*args, **kwargs)

        import inspect

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


__all__ = ("init_telemetry", "get_tracer", "trace_span", "traced", "SERVICE_NAME")
