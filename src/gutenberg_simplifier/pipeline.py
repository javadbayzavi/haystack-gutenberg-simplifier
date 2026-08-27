"""Assembly of the simplification pipeline.

Built in code rather than declared in YAML so the generators can be injected.
Tests substitute scripted stubs and exercise the full wiring with no API key and
no network; only the deployed server constructs real Anthropic generators.

Two generators, not one. Boundary detection is a judgment task over short
excerpts and simplification is a long-form writing task, so they are separate
knobs -- a later PR can move detection to a cheaper model without touching the
prose path.
"""

import httpx
from haystack import Pipeline
from haystack.core.component import Component
from haystack_integrations.components.generators.anthropic import AnthropicChatGenerator

from gutenberg_simplifier.components import (
    BoilerplateStripper,
    BoundaryDetector,
    GutenbergFetcher,
    StorySimplifier,
)
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES

DEFAULT_MODEL = "claude-opus-5"

#: One segment of a book, not a whole one. Segments are bounded by
#: simplify.DEFAULT_SEGMENT_LINES, so this ceiling is generous on purpose.
DEFAULT_MAX_TOKENS = 16_000

#: Long enough for a segment of prose, short enough that a wedged call does not
#: hold a streaming connection open indefinitely.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: The SDK retries connection errors, 429 and 5xx with exponential backoff.
#: Three attempts, so a single blip does not fail a whole book, while a real
#: outage still surfaces rather than being absorbed silently.
DEFAULT_MAX_RETRIES = 3


def default_generator(
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AnthropicChatGenerator:
    """The real generator. Reads ANTHROPIC_API_KEY from the environment."""
    return AnthropicChatGenerator(
        model=model,
        generation_kwargs={"max_tokens": max_tokens},
        timeout=timeout,
        max_retries=max_retries,
    )


def build_simplification_pipeline(
    *,
    boundary_generator: Component | None = None,
    simplify_generator: Component | None = None,
    max_bytes: int = DEFAULT_MAX_BOOK_BYTES,
    http_client: httpx.Client | None = None,
) -> Pipeline:
    """Wire fetch -> strip -> detect boundaries -> simplify.

    Args:
        boundary_generator: chat generator driving the boundary agent.
        simplify_generator: chat generator doing the rewriting.
        max_bytes: size budget applied by the fetcher.
        http_client: HTTP client for fetching; injected in tests so the whole
            pipeline runs offline.
    """
    pipeline = Pipeline()
    pipeline.add_component("fetcher", GutenbergFetcher(max_bytes=max_bytes, client=http_client))
    pipeline.add_component("stripper", BoilerplateStripper())
    pipeline.add_component(
        "boundary_detector",
        BoundaryDetector(
            boundary_generator if boundary_generator is not None else default_generator()
        ),
    )
    pipeline.add_component(
        "simplifier",
        StorySimplifier(
            simplify_generator if simplify_generator is not None else default_generator()
        ),
    )

    pipeline.connect("fetcher.book", "stripper.book")
    pipeline.connect("stripper.body", "boundary_detector.body")
    # The simplifier needs the same body the agent read, so its line numbers
    # refer to the text actually being rewritten.
    pipeline.connect("stripper.body", "simplifier.body")
    pipeline.connect("boundary_detector.boundaries", "simplifier.boundaries")
    return pipeline
