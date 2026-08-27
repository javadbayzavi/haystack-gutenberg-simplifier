"""Pipeline wiring tests. No API key, no network."""

import httpx
import pytest
from haystack.core.errors import PipelineRuntimeError

from gutenberg_simplifier.boundaries import RejectReason
from gutenberg_simplifier.components import BoilerplateStripper, GutenbergFetcher
from gutenberg_simplifier.errors import BookTooLargeError
from gutenberg_simplifier.models import RawBook
from gutenberg_simplifier.pipeline import build_simplification_pipeline
from gutenberg_simplifier.tiers import AgeTier
from tests.stubs import ScriptedChatGenerator, StubChatGenerator, decision

BOOK = "\n".join(
    [
        "The Project Gutenberg eBook of A Very Small Tale",
        "*** START OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
        "",
        "A VERY SMALL TALE",
        "",
        "Once upon a time there was a rabbit.",
        "",
        "The rabbit ate a carrot.",
        "",
        "*** END OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
        "Licence text.",
    ]
)

# Body starts at raw line 3 ("A VERY SMALL TALE"); the story runs to raw line 7.
STORY_START, STORY_END = 3, 7


def _transport(body: str = BOOK) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(body))})
        return httpx.Response(200, content=body.encode())

    return httpx.MockTransport(handle)


def _pipeline(
    boundary_script: list[object] | None = None,
    reply: str = "A rabbit ate a carrot.",
    **kwargs: object,
) -> object:
    script = boundary_script or decision(
        found=True, start_line=STORY_START, end_line=STORY_END, confidence="high"
    )
    return build_simplification_pipeline(
        boundary_generator=ScriptedChatGenerator(script),
        simplify_generator=StubChatGenerator(reply),
        http_client=httpx.Client(transport=_transport()),
        **kwargs,  # type: ignore[arg-type]
    )


def test_fetcher_component_emits_a_raw_book() -> None:
    with httpx.Client(transport=_transport()) as client:
        out = GutenbergFetcher(client=client).run(book_id=1)

    assert isinstance(out["book"], RawBook)
    assert "rabbit" in out["book"].text


def test_stripper_component_emits_body_and_text() -> None:
    raw = RawBook(book_id=1, text=BOOK, source_url="https://example.test/1", size_bytes=len(BOOK))
    out = BoilerplateStripper().run(book=raw)

    assert out["body"].markers_found is True
    assert "Licence text." not in out["text"]
    # The two outputs must agree.
    assert out["body"].text == out["text"]


def test_pipeline_runs_end_to_end_and_produces_a_story() -> None:
    result = _pipeline().run(  # type: ignore[attr-defined]
        {"fetcher": {"book_id": 1}, "simplifier": {"tier": AgeTier.PRESCHOOL}},
        include_outputs_from={"boundary_detector", "simplifier"},
    )

    assert result["boundary_detector"]["boundaries"].accepted is True
    assert "rabbit" in result["simplifier"]["story"].text.lower()
    assert result["simplifier"]["story"].segments >= 1


def test_the_simplifier_only_sees_the_story_not_the_boilerplate() -> None:
    """The reason boundaries run before simplification."""
    simplify_generator = StubChatGenerator("rewritten")
    pipeline = build_simplification_pipeline(
        boundary_generator=ScriptedChatGenerator(
            decision(found=True, start_line=STORY_START, end_line=STORY_END)
        ),
        simplify_generator=simplify_generator,
        http_client=httpx.Client(transport=_transport()),
    )

    pipeline.run({"fetcher": {"book_id": 1}})

    passage = simplify_generator.seen[0][1].text or ""
    assert "Once upon a time there was a rabbit." in passage
    assert "PROJECT GUTENBERG" not in passage
    assert "Licence text." not in passage


def test_tier_reaches_the_prompt() -> None:
    simplify_generator = StubChatGenerator("rewritten")
    pipeline = build_simplification_pipeline(
        boundary_generator=ScriptedChatGenerator(
            decision(found=True, start_line=STORY_START, end_line=STORY_END)
        ),
        simplify_generator=simplify_generator,
        http_client=httpx.Client(transport=_transport()),
    )

    pipeline.run(
        {"fetcher": {"book_id": 1}, "simplifier": {"tier": AgeTier.PRESCHOOL}},
    )

    system = simplify_generator.seen[0][0].text or ""
    assert "3 to 5" in system
    assert "at most 8 words" in system


def test_a_rejected_book_never_reaches_the_simplifier() -> None:
    """A refusal must not cost a single rewrite call."""
    simplify_generator = StubChatGenerator("should not run")
    pipeline = build_simplification_pipeline(
        boundary_generator=ScriptedChatGenerator(
            decision(found=False, reject_reason="no_story_found", notes="a manual")
        ),
        simplify_generator=simplify_generator,
        http_client=httpx.Client(transport=_transport()),
    )

    result = pipeline.run(
        {"fetcher": {"book_id": 1}}, include_outputs_from={"boundary_detector", "simplifier"}
    )

    assert result["boundary_detector"]["boundaries"].reject_reason is RejectReason.NO_STORY_FOUND
    assert result["simplifier"]["story"].segments == 0
    assert simplify_generator.seen == []  # no model call was made


def test_size_budget_is_enforced_through_the_pipeline() -> None:
    """Haystack wraps the rejection, so assert on the cause, not the type raised.

    This wrapping is why api_errors.unwrap exists: a wrapper that caught
    BookTooLargeError around pipeline.run() would never match, and every
    rejection would surface as a 500.
    """
    pipeline = _pipeline(max_bytes=10)

    with pytest.raises(PipelineRuntimeError) as caught:
        pipeline.run({"fetcher": {"book_id": 1}})  # type: ignore[attr-defined]

    assert isinstance(caught.value.__cause__, BookTooLargeError)
