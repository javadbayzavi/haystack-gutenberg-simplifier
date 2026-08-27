"""Tracing and structured logging.

Two decisions shape this module.

*Content never enters a trace.* Haystack's ``set_content_tag`` exists to attach
queries, documents and answers to spans, and it is off unless
``HAYSTACK_CONTENT_TRACING_ENABLED`` says otherwise. It stays off here, and the
tracer below does not implement it. The "content" in this pipeline is the body
of a book and the prose rewritten from it; putting either into a span would ship
the source material to whatever backend collects traces, and would attach
kilobytes to every span. Spans carry shapes and counts, never text.

*Logs and traces share an identifier.* A trace is useless for debugging a single
complaint if the log line that reported it cannot be found. Every request gets a
request id, and it is stamped on both.
"""

import json
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

# From the defining module: haystack.tracing re-exports without an explicit
# __all__, which mypy's strict mode will not follow.
from haystack.tracing.tracer import Span, Tracer, enable_tracing
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span as OtelSpan

SERVICE_NAME = "gutenberg-simplifier"

#: Set on every request and stamped into logs and spans alike.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class _Span(Span):
    """Adapts an OpenTelemetry span to Haystack's interface."""

    def __init__(self, span: OtelSpan) -> None:
        self._span = span

    def set_tag(self, key: str, value: Any) -> None:
        self._span.set_attribute(key, _as_attribute(value))

    def raw_span(self) -> Any:
        return self._span

    def set_content_tag(self, key: str, value: Any) -> None:
        """Deliberately a no-op.

        The base class already gates this behind an environment variable. This
        override makes the refusal unconditional: content here is book text,
        and a trace backend is the wrong place for it.
        """
        return


def _as_attribute(value: Any) -> str | int | float | bool:
    """OTel attributes must be scalars; anything else is summarised, not dumped."""
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple | set):
        return f"<{type(value).__name__} len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={len(value)}>"
    return f"<{type(value).__name__}>"


class OpenTelemetryTracer(Tracer):
    """Haystack tracer backed by OpenTelemetry."""

    def __init__(self, tracer: otel_trace.Tracer) -> None:
        self._tracer = tracer

    @contextmanager
    def trace(
        self,
        operation_name: str,
        tags: dict[str, Any] | None = None,
        parent_span: Span | None = None,
    ) -> Iterator[Span]:
        with self._tracer.start_as_current_span(operation_name) as raw:
            span = _Span(raw)
            span.set_tag("request.id", request_id_var.get())
            if tags:
                span.set_tags(tags)
            yield span

    def current_span(self) -> Span | None:
        raw = otel_trace.get_current_span()
        return _Span(raw) if raw is not otel_trace.INVALID_SPAN else None


def configure_tracing(endpoint: str | None = None) -> None:
    """Install the tracer. Without an endpoint, spans are created but not exported.

    That is intentional: the instrumentation should behave identically whether
    or not a collector happens to be reachable, so a missing collector never
    changes application behaviour.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))

    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

    otel_trace.set_tracer_provider(provider)
    enable_tracing(OpenTelemetryTracer(otel_trace.get_tracer(SERVICE_NAME)))


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, with the request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via `extra=` rides along, minus LogRecord's own fields.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _as_attribute(value)

        return json.dumps(payload, default=str)


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def configure_logging(level: str | None = None) -> None:
    """Send structured JSON to stdout, replacing any inherited handlers."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO").upper())
