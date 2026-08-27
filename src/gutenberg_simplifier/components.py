"""Haystack components wrapping the deterministic stage.

These are thin adapters, on purpose. The fetching and stripping logic stays in
plain functions that are testable without a pipeline; the components only
translate between those functions and Haystack's socket model. That separation
is what lets PR 3's agent reuse the same reader logic outside a pipeline run.

Errors are allowed to propagate. A component that swallowed
:class:`GutenbergSimplifierError` into a "result" would hide a decision the API
layer needs to make; :mod:`pipelines.simplify.pipeline_wrapper` translates them
into HTTP responses at the edge instead.
"""

from typing import Any

import httpx
from haystack import component

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES, fetch_book
from gutenberg_simplifier.models import BookBody, RawBook


@component
class GutenbergFetcher:
    """Downloads a book by id, enforcing the size budget."""

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BOOK_BYTES,
        client: httpx.Client | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self._client = client

    @component.output_types(book=RawBook)
    def run(self, book_id: int, max_bytes: int | None = None) -> dict[str, RawBook]:
        """Fetch ``book_id``; ``max_bytes`` overrides the configured budget."""
        return {
            "book": fetch_book(
                book_id,
                max_bytes=max_bytes if max_bytes is not None else self.max_bytes,
                client=self._client,
            )
        }


@component
class BoilerplateStripper:
    """Removes Gutenberg's licence header and footer.

    Emits the structured body *and* its flattened text: the text feeds the
    prompt builder, while the body carries the line offsets PR 3 needs.
    """

    @component.output_types(body=BookBody, text=str)
    def run(self, book: RawBook) -> dict[str, Any]:
        body = strip_gutenberg_boilerplate(book)
        return {"body": body, "text": body.text}
