"""Tracing and log formatting."""

import json
import logging
from typing import Any, cast

from haystack.tracing.tracer import is_tracing_enabled, tracer

from gutenberg_simplifier.app import create_application
from gutenberg_simplifier.observability import (
    JsonLogFormatter,
    OpenTelemetryTracer,
    request_id_var,
)


def test_our_tracer_survives_hayhooks_own_configuration() -> None:
    """Hayhooks calls configure_tracing() inside create_app().

    That call no-ops only when tracing is already enabled, so ours has to run
    first. Hayhooks then *wraps* it in a live-buffer proxy for its dashboard
    rather than replacing it, so the assertion is on the chain, not identity.
    If this fails, the ordering in create_application() regressed and spans
    lost their request ids.
    """
    create_application()

    assert is_tracing_enabled()
    assert _chain_contains_our_tracer(tracer.actual_tracer)


def _chain_contains_our_tracer(candidate: object, depth: int = 5) -> bool:
    """Follow wrapper attributes looking for our tracer."""
    if isinstance(candidate, OpenTelemetryTracer):
        return True
    if depth == 0:
        return False
    return any(
        _chain_contains_our_tracer(inner, depth - 1)
        for name in ("_inner", "_tracer", "actual_tracer", "_delegate", "_wrapped")
        if (inner := getattr(candidate, name, None)) is not None
    )


def test_spans_carry_the_request_id() -> None:
    """Without this, a trace cannot be tied to the log line that reported it."""
    create_application()
    token = request_id_var.set("req-abc123")
    try:
        with tracer.trace("test-operation") as span:
            attributes = dict(getattr(span.raw_span(), "attributes", {}) or {})
    finally:
        request_id_var.reset(token)

    assert attributes.get("request.id") == "req-abc123"


def test_content_tags_are_dropped_unconditionally() -> None:
    """Content here is book text; a trace backend is the wrong place for it."""
    create_application()

    with tracer.trace("test-operation") as span:
        span.set_content_tag("prompt", "Once upon a time there was a rabbit")
        raw = span.raw_span()
        attributes = dict(getattr(raw, "attributes", {}) or {})

    assert "prompt" not in attributes
    assert not any("rabbit" in str(value) for value in attributes.values())


def test_non_scalar_tags_are_summarised_not_dumped() -> None:
    """A whole book as a span attribute would be megabytes of noise."""
    create_application()

    with tracer.trace("test-operation") as span:
        span.set_tag("lines", ["line"] * 5000)
        raw = span.raw_span()
        attributes = dict(getattr(raw, "attributes", {}) or {})

    assert attributes.get("lines") == "<list len=5000>"


def _format(record: logging.LogRecord) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JsonLogFormatter().format(record))
    return parsed


def test_logs_are_json_with_the_request_id() -> None:
    token = request_id_var.set("req-xyz")
    try:
        payload = _format(logging.LogRecord("test", logging.INFO, __file__, 1, "hello", None, None))
    finally:
        request_id_var.reset(token)

    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-xyz"


def test_extra_fields_ride_along() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
    record.book_id = 14838

    assert _format(record)["book_id"] == 14838


def _raise_boom() -> None:
    raise ValueError("boom")


def test_exceptions_are_captured_as_a_field() -> None:
    import sys

    try:
        _raise_boom()
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    payload = _format(record)
    assert "ValueError: boom" in payload["exception"]


def test_log_output_is_one_json_object_per_line() -> None:
    """Multi-line records break every log shipper that splits on newlines."""
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "line one\nline two", None, None)

    assert "\n" not in JsonLogFormatter().format(record)


def test_a_pipeline_run_produces_a_span_per_component() -> None:
    """The acceptance criterion for this layer.

    Uses an in-memory exporter so the assertion is on real exported spans
    rather than on the tracer being installed.
    """
    import httpx
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from gutenberg_simplifier.pipeline import build_simplification_pipeline
    from tests.stubs import ScriptedChatGenerator, StubChatGenerator, decision

    create_application()  # installs our tracer

    exporter = InMemorySpanExporter()
    # get_tracer_provider() is typed as the API base class, which has no
    # add_span_processor; the SDK implementation installed above does.
    provider = cast(SdkTracerProvider, otel_trace.get_tracer_provider())
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    book = "\n".join(
        [
            "*** START OF THE PROJECT GUTENBERG EBOOK T ***",
            "",
            "A TALE",
            "",
            "Once upon a time there was a rabbit.",
            "",
            "*** END OF THE PROJECT GUTENBERG EBOOK T ***",
        ]
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(book))})
        return httpx.Response(200, content=book.encode())

    pipeline = build_simplification_pipeline(
        boundary_generator=ScriptedChatGenerator(
            decision(found=True, start_line=2, end_line=4, confidence="high")
        ),
        simplify_generator=StubChatGenerator("A rabbit."),
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    pipeline.run({"fetcher": {"book_id": 1}})

    spans = exporter.get_finished_spans()
    assert spans, "no spans were exported"

    # Haystack names every component span "haystack.component.run" and carries
    # the component in an attribute, so the assertion is on attributes.
    traced_components = {(span.attributes or {}).get("haystack.component.name") for span in spans}
    assert {"fetcher", "stripper", "boundary_detector", "simplifier"} <= traced_components

    names = {span.name for span in spans}
    assert "haystack.pipeline.run" in names
    # The agent's own loop is traced too: one span per step, plus tool calls.
    assert "haystack.agent.run" in names
    assert "haystack.agent.step.tool" in names

    # Every span must be correlatable with the log line that reported it.
    assert all("request.id" in (span.attributes or {}) for span in spans)
