"""Fetching plain-text books from Project Gutenberg.

Two production concerns shape this module:

*Cheap rejection first.* An oversized book must cost as little as possible, and
the budget is enforced long before a single token of LLM spend. Two layers do
that: a HEAD request for the advertised size, and a hard cap on the streamed
body.

Measured caveat, gutenberg.org as of 2026-08: HEAD returns 200 but sets no
``content-length``, so in practice against the real site the first layer never
fires and the streaming cap is what does the work. The HEAD is kept because it
still rejects unknown book ids without transferring a body, and because a size
gate must not depend on the server volunteering the truth -- but it is an
optimisation, not the guarantee. The guarantee is the cap.

*Retry only what is worth retrying.* A 404 is a permanent answer about this book
id and is surfaced immediately; timeouts and 5xx are transient and get a bounded
number of attempts with exponential backoff.
"""

import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
)
from gutenberg_simplifier.models import RawBook

#: Roughly the upper bound of a children's book in plain text. Provisional --
#: PR 3 measures real books and this gets retuned against that data.
DEFAULT_MAX_BOOK_BYTES = 250_000

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_ATTEMPTS = 3

_URL_TEMPLATE = "https://www.gutenberg.org/ebooks/{book_id}.txt.utf-8"
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_CHUNK_BYTES = 64 * 1024


class _TransientFetchError(Exception):
    """Internal marker: this attempt failed in a way worth retrying."""


def fetch_book(
    book_id: int,
    *,
    max_bytes: int = DEFAULT_MAX_BOOK_BYTES,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_multiplier: float = 1.0,
    client: httpx.Client | None = None,
) -> RawBook:
    """Download the plain-text edition of a Gutenberg book.

    Raises:
        BookNotFoundError: no plain-text edition exists for ``book_id``.
        BookTooLargeError: the book exceeds ``max_bytes``.
        FetchFailedError: the download failed and retrying did not help.
    """
    if book_id <= 0:
        raise ValueError(f"book_id must be positive, got {book_id}")

    url = _URL_TEMPLATE.format(book_id=book_id)
    owned_client = client is None
    http = client or httpx.Client(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "gutenberg-simplifier/0.1 (+https://github.com/javadbayzavi)"},
    )
    try:
        return _fetch_with_retries(
            http,
            url,
            book_id=book_id,
            max_bytes=max_bytes,
            max_attempts=max_attempts,
            backoff_multiplier=backoff_multiplier,
        )
    finally:
        if owned_client:
            http.close()


def _fetch_with_retries(
    http: httpx.Client,
    url: str,
    *,
    book_id: int,
    max_bytes: int,
    max_attempts: int,
    backoff_multiplier: float,
) -> RawBook:
    retrying = Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=backoff_multiplier, max=10),
        retry=retry_if_exception_type(_TransientFetchError),
        reraise=False,
    )
    try:
        for attempt in retrying:
            with attempt:
                _reject_oversized_upfront(http, url, book_id=book_id, max_bytes=max_bytes)
                return _download(http, url, book_id=book_id, max_bytes=max_bytes)
    except RetryError as exc:
        last = exc.last_attempt.exception()
        raise FetchFailedError(book_id, str(last) or "no successful attempt") from exc

    # Unreachable: Retrying either returns a value, raises, or exhausts into
    # RetryError. Present so the function has no implicit None return.
    raise FetchFailedError(book_id, "retry loop ended without a result")


def _reject_oversized_upfront(
    http: httpx.Client, url: str, *, book_id: int, max_bytes: int
) -> None:
    """Reject on the advertised size, before any body is transferred.

    A server that will not answer HEAD, or answers without a length, simply
    leaves the decision to the streaming cap in :func:`_download`. That is the
    common case against gutenberg.org, which omits ``content-length`` here; the
    call still earns its place by catching unknown ids without a body transfer.
    """
    try:
        response = http.head(url)
    except httpx.HTTPError:
        return  # HEAD is an optimisation; let the real GET report the failure.

    if response.status_code == httpx.codes.NOT_FOUND:
        raise BookNotFoundError(book_id)

    length = response.headers.get("content-length")
    if length is None or not length.isdigit():
        return
    if int(length) > max_bytes:
        raise BookTooLargeError(book_id, int(length), max_bytes)


def _download(http: httpx.Client, url: str, *, book_id: int, max_bytes: int) -> RawBook:
    try:
        with http.stream("GET", url) as response:
            _check_status(response.status_code, book_id=book_id)
            payload = _read_capped(response, book_id=book_id, max_bytes=max_bytes)
    except httpx.HTTPError as exc:
        raise _TransientFetchError(f"{type(exc).__name__}: {exc}") from exc

    return RawBook(
        book_id=book_id,
        text=_decode(payload),
        source_url=str(response.url),
        size_bytes=len(payload),
    )


def _check_status(status_code: int, *, book_id: int) -> None:
    if status_code == httpx.codes.NOT_FOUND:
        raise BookNotFoundError(book_id)
    if status_code in _RETRYABLE_STATUS:
        raise _TransientFetchError(f"HTTP {status_code}")
    if status_code >= httpx.codes.BAD_REQUEST:
        raise FetchFailedError(book_id, f"HTTP {status_code}")


def _read_capped(response: httpx.Response, *, book_id: int, max_bytes: int) -> bytes:
    """Accumulate the body, abandoning the transfer the moment it runs over.

    This is the real size guarantee -- see the module docstring on why the HEAD
    check ahead of it cannot be relied on.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise BookTooLargeError(book_id, None, max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(payload: bytes) -> str:
    """Decode as UTF-8, falling back to Latin-1 for older Gutenberg texts.

    Deliberately never uses ``errors="replace"``: silently substituting U+FFFD
    would manufacture exactly the garbled-text signal the boundary agent is
    meant to detect for real.
    """
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("latin-1")
