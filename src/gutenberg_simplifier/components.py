"""Haystack components wrapping the deterministic stage.

These are thin adapters, on purpose. The fetching and stripping logic stays in
plain functions that are testable without a pipeline; the components only
translate between those functions and Haystack's socket model. That separation
is what lets the boundary agent reuse the same reader logic outside a pipeline
run.

Errors are allowed to propagate. A component that swallowed
:class:`GutenbergSimplifierError` into a "result" would hide a decision the API
layer needs to make; :mod:`pipelines.simplify.pipeline_wrapper` translates them
into HTTP responses at the edge instead.
"""

from collections.abc import Callable
from typing import Any

import httpx
from haystack import component
from haystack.core.component import Component

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.boundaries import BoundaryState, detect_boundaries
from gutenberg_simplifier.chunking import ChunkReader
from gutenberg_simplifier.fallback import apply_boundary_fallback
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES, fetch_book
from gutenberg_simplifier.models import BookBody, RawBook
from gutenberg_simplifier.results import Usage
from gutenberg_simplifier.simplify import (
    DEFAULT_SEGMENT_LINES,
    SimplifiedStory,
    simplify_story,
)
from gutenberg_simplifier.tiers import AgeTier


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
        prompt builder, while the body carries the line offsets the boundary agent
    reports against.
    """

    @component.output_types(body=BookBody, text=str)
    def run(self, book: RawBook) -> dict[str, Any]:
        body = strip_gutenberg_boilerplate(book)
        return {"body": body, "text": body.text}


@component
class BoundaryDetector:
    """Locates the story inside a body using the boundary agent.

    Emits the state rather than a trimmed body: whether an ambiguous or refused
    result should end the request or fall back is a policy decision, and it
    belongs to the caller, not to a component in the middle of a pipeline.
    """

    def __init__(
        self,
        chat_generator: Component,
        *,
        reader_factory: Callable[[BookBody], ChunkReader] | None = None,
        fallback: bool = True,
    ) -> None:
        self.chat_generator = chat_generator
        self._reader_factory = reader_factory
        self.fallback = fallback

    @component.output_types(boundaries=BoundaryState)
    def run(self, body: BookBody) -> dict[str, BoundaryState]:
        boundaries = detect_boundaries(
            body,
            self.chat_generator,
            reader_factory=self._reader_factory,
        )
        if self.fallback:
            boundaries = apply_boundary_fallback(body, boundaries)
        return {"boundaries": boundaries}


@component
class StorySimplifier:
    """Rewrites the resolved story for a reading age.

    A rejected book short-circuits to an empty result rather than being routed
    around. Haystack's branching would need a router component and two extra
    edges to express "skip this step", which is more machinery than a guard
    clause for something that costs nothing when skipped.
    """

    def __init__(
        self,
        chat_generator: Component,
        *,
        max_lines: int = DEFAULT_SEGMENT_LINES,
    ) -> None:
        self.chat_generator = chat_generator
        self.max_lines = max_lines

    @component.output_types(story=SimplifiedStory)
    def run(
        self,
        body: BookBody,
        boundaries: BoundaryState,
        tier: AgeTier = AgeTier.EARLY_READER,
    ) -> dict[str, SimplifiedStory]:
        if not boundaries.accepted:
            return {"story": SimplifiedStory(text="", usage=Usage(), segments=0)}

        return {
            "story": simplify_story(
                body,
                boundaries,
                tier,
                self.chat_generator,
                max_lines=self.max_lines,
            )
        }
