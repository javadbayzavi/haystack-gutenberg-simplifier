"""Fetcher tests, driven entirely through httpx's MockTransport.

No network, no extra mocking dependency: every case below is a scripted
conversation with a fake Gutenberg.
"""

import httpx
import pytest

from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
)
from gutenberg_simplifier.fetch import fetch_book

BOOK_TEXT = "Once upon a time.\nThe end.\n"


def _client(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport, follow_redirects=True)


def _serve(
    body: bytes = BOOK_TEXT.encode(),
    *,
    status: int = 200,
    head_status: int | None = None,
    content_length: str | None = None,
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    """A transport that answers HEAD with metadata and GET with the body."""

    def handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.method)
        if request.method == "HEAD":
            headers = {}
            if content_length is not None:
                headers["content-length"] = content_length
            elif head_status is None and status == 200:
                headers["content-length"] = str(len(body))
            return httpx.Response(
                head_status if head_status is not None else status, headers=headers
            )
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handle)


def test_returns_the_book_text() -> None:
    with _client(_serve()) as client:
        book = fetch_book(11, client=client)

    assert book.book_id == 11
    assert book.text == BOOK_TEXT
    assert book.size_bytes == len(BOOK_TEXT.encode())
    assert book.lines == ("Once upon a time.", "The end.")


def test_missing_book_is_not_found() -> None:
    with _client(_serve(status=404)) as client, pytest.raises(BookNotFoundError) as caught:
        fetch_book(999_999, client=client)

    assert caught.value.book_id == 999_999


def test_not_found_is_not_retried() -> None:
    calls: list[str] = []
    with _client(_serve(status=404, calls=calls)) as client, pytest.raises(BookNotFoundError):
        fetch_book(999_999, client=client, backoff_multiplier=0)

    # One HEAD, and no second attempt: a 404 is a permanent answer.
    assert calls == ["HEAD"]


def test_advertised_size_is_rejected_before_download() -> None:
    calls: list[str] = []
    transport = _serve(content_length="900000", calls=calls)

    with _client(transport) as client, pytest.raises(BookTooLargeError) as caught:
        fetch_book(11, client=client, max_bytes=1000, backoff_multiplier=0)

    assert caught.value.size_bytes == 900_000
    assert caught.value.max_bytes == 1000
    # The body was never transferred -- this is the whole point of the gate.
    assert "GET" not in calls


def test_unreported_size_is_capped_while_streaming() -> None:
    """A server that hides the length still cannot blow the budget."""
    oversized = b"x" * 5000
    transport = _serve(oversized, content_length=None, head_status=405)

    with _client(transport) as client, pytest.raises(BookTooLargeError) as caught:
        fetch_book(11, client=client, max_bytes=1000, backoff_multiplier=0)

    assert caught.value.size_bytes is None


def test_transient_failure_is_retried_then_succeeds() -> None:
    attempts = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(BOOK_TEXT))})
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=BOOK_TEXT.encode())

    with _client(httpx.MockTransport(handle)) as client:
        book = fetch_book(11, client=client, backoff_multiplier=0)

    assert attempts["n"] == 2
    assert book.text == BOOK_TEXT


def test_persistent_server_error_fails_after_max_attempts() -> None:
    calls: list[str] = []
    transport = _serve(status=503, content_length=None, calls=calls)

    with _client(transport) as client, pytest.raises(FetchFailedError) as caught:
        fetch_book(11, client=client, max_attempts=3, backoff_multiplier=0)

    assert "503" in str(caught.value)
    assert calls.count("GET") == 3


def test_network_error_is_wrapped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with _client(httpx.MockTransport(handle)) as client, pytest.raises(FetchFailedError):
        fetch_book(11, client=client, max_attempts=2, backoff_multiplier=0)


def test_client_error_is_not_retried() -> None:
    calls: list[str] = []
    transport = _serve(status=403, content_length=None, calls=calls)

    with _client(transport) as client, pytest.raises(FetchFailedError) as caught:
        fetch_book(11, client=client, max_attempts=3, backoff_multiplier=0)

    assert "403" in str(caught.value)
    assert calls.count("GET") == 1


def test_latin1_text_decodes_without_replacement_characters() -> None:
    # A naive utf-8 decode with errors="replace" would turn this into U+FFFD,
    # which is exactly the garbled-text signal the agent must not see falsely.
    payload = "Café near the hedge.\n".encode("latin-1")
    with _client(_serve(payload)) as client:
        book = fetch_book(11, client=client)

    assert "�" not in book.text
    assert "Caf" in book.text


def test_rejects_non_positive_book_id() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        fetch_book(0)


def test_gzipped_content_length_is_not_trusted_as_the_size() -> None:
    """A compressed Content-Length is not comparable to a decoded-byte budget.

    Measured against gutenberg.org: plain text compresses ~2.7:1, so believing
    the advertised length would admit books roughly three times over budget.
    The advertised 900 here is under the 1000 budget; the decoded body is not.
    """
    body = b"x" * 5000

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"content-length": "900", "content-encoding": "gzip"}
            )
        return httpx.Response(200, content=body)

    with _client(httpx.MockTransport(handle)) as client, pytest.raises(BookTooLargeError) as caught:
        fetch_book(11, client=client, max_bytes=1000, backoff_multiplier=0)

    # Rejected by the streaming cap (size unknown), not by the HEAD gate.
    assert caught.value.size_bytes is None


def test_uncompressed_content_length_is_still_trusted() -> None:
    """The HEAD gate must keep working for origins that report decoded sizes."""
    calls: list[str] = []
    with (
        _client(_serve(content_length="900000", calls=calls)) as client,
        pytest.raises(BookTooLargeError) as caught,
    ):
        fetch_book(11, client=client, max_bytes=1000, backoff_multiplier=0)

    assert caught.value.size_bytes == 900_000
    assert "GET" not in calls
