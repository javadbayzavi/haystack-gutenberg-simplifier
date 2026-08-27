"""Streaming behaviour: what streams, what does not, and what happens on failure."""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from gutenberg_simplifier.errors import BookNotFoundError, BookTooLargeError
from gutenberg_simplifier.models import RawBook
from gutenberg_simplifier.streaming import FetchFn, stream_simplification
from gutenberg_simplifier.tiers import AgeTier
from tests.stubs import AsyncStreamingChatGenerator, ScriptedChatGenerator, decision

BOOK = "\n".join(
    [
        "*** START OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
        "",
        "A VERY SMALL TALE",
        "",
        "Once upon a time there was a rabbit.",
        "",
        "*** END OF THE PROJECT GUTENBERG EBOOK A VERY SMALL TALE ***",
    ]
)
STORY_START, STORY_END = 2, 4


def _raw(text: str = BOOK) -> RawBook:
    return RawBook(book_id=1, text=text, source_url="https://example.test/1", size_bytes=len(text))


def _fetch_ok(book_id: int, max_bytes: int) -> RawBook:
    return _raw()


async def _collect(agen: AsyncGenerator[str, None]) -> list[str]:
    return [chunk async for chunk in agen]


def _stream(
    *,
    boundary_script: list[object] | None = None,
    reply: str = "The rabbit ran home.",
    fetch: FetchFn = _fetch_ok,
    simplify_generator: AsyncStreamingChatGenerator | None = None,
    tier: AgeTier = AgeTier.PRESCHOOL,
) -> AsyncGenerator[str, None]:
    script = boundary_script or decision(
        found=True, start_line=STORY_START, end_line=STORY_END, confidence="high"
    )
    return stream_simplification(
        1,
        tier,
        boundary_generator=ScriptedChatGenerator(script),
        simplify_generator=simplify_generator or AsyncStreamingChatGenerator(reply),
        fetch=fetch,
    )


def test_prose_arrives_in_multiple_chunks_not_one_blob() -> None:
    """If it arrives whole, it is not streaming."""
    chunks = asyncio.run(_collect(_stream(reply="The small brown rabbit ran home again")))

    prose = [c for c in chunks if not c.startswith("_") and c.strip()]
    assert len(prose) > 3


def test_progress_precedes_the_story() -> None:
    chunks = asyncio.run(_collect(_stream()))

    assert chunks[0].startswith("_Fetching book 1")
    progress = [c for c in chunks if c.startswith("_")]
    assert any("Looking for the story" in c for c in progress)
    assert any("Rewriting for ages 3 to 5" in c for c in progress)


def test_the_boundary_agents_own_output_never_streams() -> None:
    """Progress reports that a phase happened, never what the model said."""
    chunks = asyncio.run(
        _collect(
            _stream(
                boundary_script=decision(
                    found=True,
                    start_line=STORY_START,
                    end_line=STORY_END,
                    notes="SECRET-AGENT-REASONING",
                )
            )
        )
    )

    assert "SECRET-AGENT-REASONING" not in "".join(chunks)


def test_source_text_is_never_echoed_by_the_progress_lines() -> None:
    chunks = asyncio.run(_collect(_stream()))
    progress = "".join(c for c in chunks if c.startswith("_"))

    assert "Once upon a time" not in progress


def test_a_rejected_book_explains_itself_and_never_rewrites() -> None:
    simplify_generator = AsyncStreamingChatGenerator("should not run")
    chunks = asyncio.run(
        _collect(
            _stream(
                boundary_script=decision(
                    found=False, reject_reason="no_story_found", notes="It is a manual."
                ),
                simplify_generator=simplify_generator,
            )
        )
    )

    text = "".join(chunks)
    assert "no_story_found" in text
    assert "It is a manual." in text
    assert simplify_generator.seen == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BookNotFoundError(999), "No plain-text edition"),
        (BookTooLargeError(1, 900_000, 250_000), "over the 250000 byte budget"),
    ],
)
def test_fetch_failures_become_sentences_not_status_codes(error: Exception, expected: str) -> None:
    """A stream has committed to 200 before the failure is known."""

    def failing_fetch(book_id: int, max_bytes: int) -> RawBook:
        raise error

    chunks = asyncio.run(_collect(_stream(fetch=failing_fetch)))

    assert expected in "".join(chunks)


def test_tier_guidance_reaches_the_streamed_call() -> None:
    simplify_generator = AsyncStreamingChatGenerator("out")
    asyncio.run(_collect(_stream(simplify_generator=simplify_generator, tier=AgeTier.MIDDLE_GRADE)))

    system = simplify_generator.seen[0][0].text or ""
    assert "9 to 11" in system


def test_the_passage_not_the_boilerplate_reaches_the_model() -> None:
    simplify_generator = AsyncStreamingChatGenerator("out")
    asyncio.run(_collect(_stream(simplify_generator=simplify_generator)))

    passage = simplify_generator.seen[0][1].text or ""
    assert "Once upon a time there was a rabbit." in passage
    assert "PROJECT GUTENBERG" not in passage


def test_an_empty_range_is_reported_not_streamed_as_nothing() -> None:
    chunks = asyncio.run(
        _collect(_stream(boundary_script=decision(found=True, start_line=500, end_line=600)))
    )

    assert "did not contain any text" in "".join(chunks)


def test_a_mid_stream_failure_is_marked_not_silently_truncated() -> None:
    """A partial story that just stops reads exactly like a finished one.

    Re-raising would drop the connection and leave the reader unable to tell
    the difference, so the stream says so and the real cause goes to the log.
    """
    failing = AsyncStreamingChatGenerator("x", fail_with=RuntimeError("model exploded"))

    chunks = asyncio.run(_collect(_stream(simplify_generator=failing)))
    text = "".join(chunks)

    assert "stopped partway" in text
    assert "incomplete" in text
    assert "model exploded" not in text  # internals stay out of the reader's view


def test_a_boundary_phase_failure_degrades_to_a_sentence() -> None:
    """Nothing of value has streamed yet, so this can fail cleanly."""

    class Exploding:
        # Mirrors the real generator's signature; Agent inspects it for `tools`.
        def run(
            self,
            messages: object,
            streaming_callback: object = None,
            generation_kwargs: object = None,
            tools: object = None,
        ) -> dict[str, object]:
            raise RuntimeError("boundary model down")

    chunks = asyncio.run(
        _collect(
            stream_simplification(
                1,
                AgeTier.PRESCHOOL,
                boundary_generator=Exploding(),
                simplify_generator=AsyncStreamingChatGenerator("out"),
                fetch=_fetch_ok,
            )
        )
    )

    assert "could not read this book" in "".join(chunks)


def test_abandoning_the_stream_cancels_the_in_flight_call() -> None:
    """A client disconnecting must not leave tokens billing into a closed socket."""
    generator = AsyncStreamingChatGenerator(" ".join(f"word{i}" for i in range(200)))

    async def take_two() -> None:
        agen = _stream(simplify_generator=generator)
        seen = 0
        async for _ in agen:
            seen += 1
            if seen == 5:
                break
        await agen.aclose()

    asyncio.run(take_two())
    assert generator.cancelled == 1


def test_both_paths_build_byte_identical_prompts() -> None:
    """The JSON and streaming paths must ask the model the same thing.

    They used to assemble these prompts independently, which is the shape that
    quietly diverges: a change to the tier prompt or the continuity hint would
    have reached one endpoint and not the other, and nothing would have failed.
    Both now share the builders in simplify.py, and this asserts it stays true.
    """
    from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
    from gutenberg_simplifier.boundaries import BoundaryState, Confidence
    from gutenberg_simplifier.simplify import simplify_story
    from tests.stubs import StubChatGenerator

    # A body long enough to need more than one segment, so the continuity hint
    # is exercised and not just the first prompt.
    lines = [f"Sentence number {i} of the tale." if i % 3 else "" for i in range(40)]
    text = "\n".join(
        [
            "*** START OF THE PROJECT GUTENBERG EBOOK LONG ***",
            "",
            *lines,
            "",
            "*** END OF THE PROJECT GUTENBERG EBOOK LONG ***",
        ]
    )
    raw = RawBook(book_id=1, text=text, source_url="https://example.test/1", size_bytes=len(text))
    body = strip_gutenberg_boilerplate(raw)
    boundaries = BoundaryState(
        start_line=body.start_line,
        end_line=body.start_line + body.line_count - 1,
        reject_reason=None,
        confidence=Confidence.HIGH,
        notes="",
        iterations_used=1,
    )

    reply = "The rewritten sentence."
    sync_generator = StubChatGenerator(reply)
    simplify_story(body, boundaries, AgeTier.PRESCHOOL, sync_generator, max_lines=12)

    stream_generator = AsyncStreamingChatGenerator(reply)
    asyncio.run(
        _collect(
            stream_simplification(
                1,
                AgeTier.PRESCHOOL,
                boundary_generator=ScriptedChatGenerator(
                    decision(
                        found=True,
                        start_line=boundaries.start_line,
                        end_line=boundaries.end_line,
                        confidence="high",
                    )
                ),
                simplify_generator=stream_generator,
                fetch=lambda book_id, max_bytes: raw,
                max_lines=12,
            )
        )
    )

    assert len(sync_generator.seen) > 1, "test needs multiple segments to be meaningful"
    assert len(sync_generator.seen) == len(stream_generator.seen)
    for sync_messages, stream_messages in zip(
        sync_generator.seen, stream_generator.seen, strict=True
    ):
        assert [m.text for m in sync_messages] == [m.text for m in stream_messages]
