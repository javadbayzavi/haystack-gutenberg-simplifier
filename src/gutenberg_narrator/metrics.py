"""Prometheus metrics for narration.

The labels that matter here are different from the simplifier's. There the
question was *why* a book was refused; here it is whether synthesis is keeping
up, so the headline metric is the realtime factor -- seconds of compute per
second of audio produced. Below 1.0 the service can stream faster than a
listener consumes; above it, streaming stalls and the design needs revisiting.

That single number is what decides whether this service can stream at all on a
given deployment, so it is measured rather than assumed.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "narrate_requests_total",
    "Narration requests by outcome.",
    ["outcome"],
)

DURATION = Histogram(
    "narrate_request_duration_seconds",
    "Wall time for a narration request.",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

AUDIO_SECONDS = Counter(
    "narrate_audio_seconds_total",
    "Seconds of audio produced.",
)

REALTIME_FACTOR = Histogram(
    "narrate_realtime_factor",
    "Compute seconds per second of audio. Above 1.0 means synthesis is slower "
    "than playback and cannot stream.",
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2, 5, 10),
)

UNITS = Histogram(
    "narrate_units_per_request",
    "Synthesis units per request.",
    buckets=(1, 2, 5, 10, 25, 50, 100, 250),
)

HTTP_ERRORS = Counter(
    "narrate_http_errors_total",
    "Requests rejected before synthesis.",
    ["status_code"],
)

ENGINE_FAILURES = Counter(
    "narrate_engine_failures_total",
    "Synthesis failures, including those that truncated a stream in progress.",
)


def record_request(*, outcome: str, duration: float, audio_seconds: float, units: int) -> None:
    REQUESTS.labels(outcome=outcome).inc()
    DURATION.observe(duration)
    UNITS.observe(units)
    if audio_seconds > 0:
        AUDIO_SECONDS.inc(audio_seconds)
        REALTIME_FACTOR.observe(duration / audio_seconds)


def record_http_error(status_code: int) -> None:
    HTTP_ERRORS.labels(status_code=str(status_code)).inc()


def record_engine_failure() -> None:
    ENGINE_FAILURES.inc()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
