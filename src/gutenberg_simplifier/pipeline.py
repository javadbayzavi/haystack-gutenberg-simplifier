"""Assembly of the simplification pipeline.

The pipeline is built in code rather than declared in YAML so the generator can
be injected. Tests substitute a stub chat generator and exercise the full wiring
with no API key and no network; only the deployed server constructs a real
:class:`AnthropicChatGenerator`.
"""

import httpx
from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.core.component import Component
from haystack_integrations.components.generators.anthropic import AnthropicChatGenerator

from gutenberg_simplifier.components import BoilerplateStripper, GutenbergFetcher
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES
from gutenberg_simplifier.prompts import simplification_template

DEFAULT_MODEL = "claude-opus-5"

#: One pass over a whole children's book. PR 4 chunks the body and this ceiling
#: stops being the thing that bounds output length.
DEFAULT_MAX_TOKENS = 16_000


def default_generator(
    model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS
) -> AnthropicChatGenerator:
    """The real generator. Reads ANTHROPIC_API_KEY from the environment."""
    return AnthropicChatGenerator(model=model, generation_kwargs={"max_tokens": max_tokens})


def build_simplification_pipeline(
    *,
    generator: Component | None = None,
    max_bytes: int = DEFAULT_MAX_BOOK_BYTES,
    http_client: httpx.Client | None = None,
) -> Pipeline:
    """Wire fetch -> strip -> prompt -> generate.

    Args:
        generator: chat generator to use; defaults to Anthropic. Injected in
            tests so the wiring can be verified without an API key.
        max_bytes: size budget applied by the fetcher.
        http_client: HTTP client for fetching. Injected in tests so the whole
            pipeline can run offline; production passes nothing and the fetcher
            manages its own client per call.
    """
    pipeline = Pipeline()
    pipeline.add_component("fetcher", GutenbergFetcher(max_bytes=max_bytes, client=http_client))
    pipeline.add_component("stripper", BoilerplateStripper())
    pipeline.add_component(
        "prompt_builder",
        ChatPromptBuilder(
            template=simplification_template(),
            variables=["story"],
            required_variables=["story"],
        ),
    )
    pipeline.add_component("generator", generator if generator is not None else default_generator())

    pipeline.connect("fetcher.book", "stripper.book")
    pipeline.connect("stripper.text", "prompt_builder.story")
    pipeline.connect("prompt_builder.prompt", "generator.messages")
    return pipeline
