"""Prometheus metrics.

Labels are chosen so a bad week can be explained without opening a trace. The
one that matters most is ``reject_reason``: a rise in ``no_story_found`` means
people are asking for the wrong books, a rise in ``budget_exhausted`` means the
iteration budget is mistuned, and a rise in ``corrupted_text`` means Gutenberg
served something odd. Those are three completely different responses to what
would otherwise look like one "rejection rate" going up.

Label values come from closed enums, never from user input. A label whose values
are unbounded turns a metrics backend into a memory leak.
"""

from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "simplify_requests_total",
    "Simplification requests by outcome.",
    ["status", "reject_reason"],
)

DURATION = Histogram(
    "simplify_request_duration_seconds",
    "Wall time for a simplification request.",
    ["status"],
    # A request spans several model calls, so the usual web buckets are useless.
    buckets=(1, 2.5, 5, 10, 20, 40, 80, 160, 320),
)

TOKENS = Counter(
    "simplify_tokens_total",
    "Model tokens consumed.",
    ["direction"],
)

MODEL_CALLS = Counter(
    "simplify_model_calls_total",
    "Model calls made across all phases.",
)

BOUNDARY_ITERATIONS = Histogram(
    "simplify_boundary_iterations",
    "Chunks read by the boundary agent before it decided.",
    buckets=(1, 2, 3, 5, 8, 13, 21, 34, 40),
)

FALLBACKS = Counter(
    "simplify_boundary_fallbacks_total",
    "Requests served with guessed boundaries after the search failed.",
)

HTTP_ERRORS = Counter(
    "simplify_http_errors_total",
    "Requests that failed before reaching a model.",
    ["status_code"],
)


def record_result(
    status: str, reject_reason: str | None, duration: float, metadata: dict[str, Any]
) -> None:
    """Record one completed request."""
    REQUESTS.labels(status=status, reject_reason=reject_reason or "none").inc()
    DURATION.labels(status=status).observe(duration)
    BOUNDARY_ITERATIONS.observe(metadata.get("boundary_iterations", 0))

    if metadata.get("boundary_fallback_applied"):
        FALLBACKS.inc()

    usage = metadata.get("usage") or {}
    TOKENS.labels(direction="input").inc(usage.get("input_tokens", 0))
    TOKENS.labels(direction="output").inc(usage.get("output_tokens", 0))
    MODEL_CALLS.inc(usage.get("model_calls", 0))


def record_http_error(status_code: int) -> None:
    HTTP_ERRORS.labels(status_code=str(status_code)).inc()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
