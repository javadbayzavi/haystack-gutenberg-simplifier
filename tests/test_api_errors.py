"""Error-to-status mapping, including the cause-chain unwrapping."""

import httpx
import pytest
from haystack.core.errors import PipelineRuntimeError

from gutenberg_simplifier.api_errors import to_http_exception, unwrap
from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
)
from gutenberg_simplifier.pipeline import build_simplification_pipeline
from tests.stubs import StubChatGenerator


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (BookNotFoundError(999), 404),
        (BookTooLargeError(1, 900_000, 250_000), 413),
        (FetchFailedError(1, "connection reset"), 502),
        (RuntimeError("something unexpected"), 500),
    ],
)
def test_maps_each_error_to_its_status(error: Exception, expected_status: int) -> None:
    assert to_http_exception(error).status_code == expected_status


def test_unexpected_errors_do_not_leak_internals() -> None:
    detail = to_http_exception(RuntimeError("secret internal detail")).detail
    assert "secret internal detail" not in str(detail)


def test_maps_through_a_wrapped_cause_chain() -> None:
    wrapped = PipelineRuntimeError("fetcher", object, "component failed")
    wrapped.__cause__ = BookNotFoundError(999)

    assert to_http_exception(wrapped).status_code == 404


def test_unwrap_survives_a_self_referential_chain() -> None:
    """A malformed cause cycle must not hang the request."""
    error = RuntimeError("loop")
    error.__cause__ = error

    assert unwrap(error) is error


def test_real_pipeline_rejection_maps_to_413() -> None:
    """End to end: the error a real pipeline run raises maps correctly."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = b"x" * 5000
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(body))})
        return httpx.Response(200, content=body)

    pipeline = build_simplification_pipeline(
        generator=StubChatGenerator(),
        max_bytes=10,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    with pytest.raises(PipelineRuntimeError) as caught:
        pipeline.run({"fetcher": {"book_id": 1}})

    assert to_http_exception(caught.value).status_code == 413
