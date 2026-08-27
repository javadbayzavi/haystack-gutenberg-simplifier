"""Pipeline wiring tests. No API key, no network."""

import httpx
import pytest
from haystack.dataclasses import ChatMessage

from gutenberg_simplifier.components import BoilerplateStripper, GutenbergFetcher
from gutenberg_simplifier.models import RawBook
from gutenberg_simplifier.pipeline import build_simplification_pipeline
from tests.stubs import StubChatGenerator

BOOK = "\n".join(
    [
        "The Project Gutenberg eBook of A Very Small Tale",
        "*** START OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
        "",
        "Once upon a time there was a rabbit.",
        "",
        "*** END OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
        "Licence text.",
    ]
)


def _transport(body: str = BOOK) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(body))})
        return httpx.Response(200, content=body.encode())

    return httpx.MockTransport(handle)


def test_fetcher_component_emits_a_raw_book() -> None:
    with httpx.Client(transport=_transport()) as client:
        out = GutenbergFetcher(client=client).run(book_id=1)

    assert isinstance(out["book"], RawBook)
    assert "rabbit" in out["book"].text


def test_stripper_component_emits_body_and_text() -> None:
    raw = RawBook(book_id=1, text=BOOK, source_url="https://example.test/1", size_bytes=len(BOOK))
    out = BoilerplateStripper().run(book=raw)

    assert out["text"] == "Once upon a time there was a rabbit."
    assert out["body"].markers_found is True
    # The two outputs must agree; the prompt sees exactly what the body holds.
    assert out["body"].text == out["text"]


def test_pipeline_runs_end_to_end_with_a_stub_generator() -> None:
    generator = StubChatGenerator()
    pipeline = build_simplification_pipeline(
        generator=generator, http_client=httpx.Client(transport=_transport())
    )

    result = pipeline.run({"fetcher": {"book_id": 1}}, include_outputs_from={"stripper"})

    assert result["generator"]["replies"][0].text == "A rabbit ate a carrot. The end."
    assert result["stripper"]["body"].line_count == 1


def test_the_prompt_carries_the_stripped_story_not_the_licence() -> None:
    """The whole reason stripping happens before the prompt builder."""
    generator = StubChatGenerator()
    pipeline = build_simplification_pipeline(
        generator=generator, http_client=httpx.Client(transport=_transport())
    )

    pipeline.run({"fetcher": {"book_id": 1}})

    sent = generator.seen[0]
    assert len(sent) == 2  # system + user
    user_text = sent[1].text or ""
    assert "Once upon a time there was a rabbit." in user_text
    assert "PROJECT GUTENBERG" not in user_text
    assert "Licence text." not in user_text


def test_system_prompt_survives_templating() -> None:
    generator = StubChatGenerator()
    pipeline = build_simplification_pipeline(
        generator=generator, http_client=httpx.Client(transport=_transport())
    )

    pipeline.run({"fetcher": {"book_id": 1}})

    system: ChatMessage = generator.seen[0][0]
    assert system.is_from("system")
    assert "short sentences" in (system.text or "")


def test_size_budget_is_enforced_through_the_pipeline() -> None:
    """Haystack wraps the rejection, so assert on the cause, not the type raised.

    This wrapping is why api_errors.unwrap exists: a wrapper that caught
    BookTooLargeError around pipeline.run() would never match, and every
    rejection would surface as a 500.
    """
    from haystack.core.errors import PipelineRuntimeError

    from gutenberg_simplifier.errors import BookTooLargeError

    pipeline = build_simplification_pipeline(
        generator=StubChatGenerator(),
        max_bytes=10,
        http_client=httpx.Client(transport=_transport()),
    )

    with pytest.raises(PipelineRuntimeError) as caught:
        pipeline.run({"fetcher": {"book_id": 1}})

    assert isinstance(caught.value.__cause__, BookTooLargeError)
