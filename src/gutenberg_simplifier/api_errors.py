"""Translation of pipeline failures into HTTP responses.

Haystack wraps any exception a component raises in a ``PipelineRuntimeError``,
chaining the original on ``__cause__``. Catching :class:`GutenbergSimplifierError`
directly around ``pipeline.run()`` therefore never matches, and every deliberate
rejection would surface as a generic 500. This module unwraps the chain and maps
the original error to the status code that actually describes it.

Lives here rather than in the Hayhooks wrapper so it can be unit tested without
standing up a server.
"""

from fastapi import HTTPException

from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
)

#: 413 for an oversized book: the request names work we refuse to do.
#: 502 for a fetch failure: the upstream failed, not the caller.
_STATUS_BY_ERROR: list[tuple[type[Exception], int]] = [
    (BookNotFoundError, 404),
    (BookTooLargeError, 413),
    (FetchFailedError, 502),
]


def unwrap(error: BaseException) -> BaseException:
    """Follow ``__cause__`` to the originating exception."""
    seen: set[int] = set()
    current: BaseException = error
    while current.__cause__ is not None and id(current) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def to_http_exception(error: BaseException) -> HTTPException:
    """Map a pipeline failure to an HTTPException.

    Anything unrecognised becomes a 500 with a generic message: an unexpected
    error must not leak internals through the API surface.
    """
    original = unwrap(error)
    for error_type, status_code in _STATUS_BY_ERROR:
        if isinstance(original, error_type):
            return HTTPException(status_code=status_code, detail=str(original))
    return HTTPException(status_code=500, detail="Internal error while simplifying the book")
