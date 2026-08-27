"""Typed failures for the deterministic (pre-LLM) stage.

Every failure here is a *decision* the pipeline can report, not an unexpected
crash: each carries enough structure for the API layer to turn it into a
meaningful rejection rather than a 500.
"""


class GutenbergSimplifierError(Exception):
    """Base class for every error this package raises deliberately."""


class BookNotFoundError(GutenbergSimplifierError):
    """No plain-text edition exists for this book id."""

    def __init__(self, book_id: int) -> None:
        super().__init__(f"No plain-text edition found for Gutenberg book id {book_id}")
        self.book_id = book_id


class BookTooLargeError(GutenbergSimplifierError):
    """The book exceeds the size budget.

    Raised before the full body is downloaded whenever the server tells us the
    size up front, so an oversized book costs one HEAD request rather than a
    full download plus an LLM pass.
    """

    def __init__(self, book_id: int, size_bytes: int | None, max_bytes: int) -> None:
        actual = f"{size_bytes} bytes" if size_bytes is not None else "an unreported size"
        super().__init__(
            f"Gutenberg book id {book_id} has {actual}, over the {max_bytes} byte budget"
        )
        self.book_id = book_id
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class FetchFailedError(GutenbergSimplifierError):
    """The fetch failed for a reason that is not the caller's fault.

    Network trouble, or an upstream status we retried and never recovered from.
    """

    def __init__(self, book_id: int, reason: str) -> None:
        super().__init__(f"Failed to fetch Gutenberg book id {book_id}: {reason}")
        self.book_id = book_id
        self.reason = reason
