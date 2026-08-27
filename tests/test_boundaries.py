"""Boundary agent tests. No API key, no network -- the generator is scripted.

The point of these tests is not that the model gets the right answer; it is
that every way the model can misbehave produces a bounded, well-formed result.
"""

import pytest

from gutenberg_simplifier.boundaries import (
    BoundaryState,
    Confidence,
    RejectReason,
    detect_boundaries,
)
from gutenberg_simplifier.chunking import ChunkReader
from gutenberg_simplifier.models import BookBody
from tests.stubs import ScriptedChatGenerator, decision, reads


def _body(line_count: int = 120) -> BookBody:
    return BookBody(
        book_id=1,
        lines=tuple(f"line {i}" for i in range(line_count)),
        start_line=10,
        markers_found=True,
    )


def _small_reader(body: BookBody) -> ChunkReader:
    return ChunkReader(body, chunk_lines=10, overlap_lines=2, max_iterations=5)


def _run(script: list[object], body: BookBody | None = None) -> BoundaryState:
    return detect_boundaries(
        body or _body(),
        ScriptedChatGenerator(script),
        reader_factory=_small_reader,
    )


def test_records_boundaries_after_reading() -> None:
    state = _run(
        reads(2)
        + decision(found=True, start_line=14, end_line=88, confidence="high", notes="Story found.")
    )

    assert state.accepted is True
    assert (state.start_line, state.end_line) == (14, 88)
    assert state.confidence is Confidence.HIGH
    assert state.iterations_used == 2


def test_a_model_that_never_stops_still_terminates() -> None:
    """The property the whole design exists for.

    This generator asks for another chunk forever. The reader stops serving
    once its budget is spent and the agent's step ceiling ends the run, so the
    call returns instead of hanging.
    """
    generator = ScriptedChatGenerator(reads(1))  # last entry repeats forever

    state = detect_boundaries(_body(10_000), generator, reader_factory=_small_reader)

    assert state.accepted is False
    assert state.reject_reason is RejectReason.BUDGET_EXHAUSTED
    assert state.iterations_used == 5  # the reader's budget, not the model's will
    assert generator.calls <= 5 + 5 + 1  # max_iterations + STEP_SLACK, bounded


def test_reader_budget_caps_reads_even_on_a_huge_book() -> None:
    state = _run(reads(50) + decision(found=True, start_line=1, end_line=2), body=_body(10_000))

    assert state.iterations_used == 5


@pytest.mark.parametrize(
    "reason",
    [
        RejectReason.CORRUPTED_TEXT,
        RejectReason.INAPPROPRIATE_CONTENT,
        RejectReason.NO_STORY_FOUND,
        RejectReason.AMBIGUOUS_BOUNDARIES,
    ],
)
def test_each_model_rejection_reason_round_trips(reason: RejectReason) -> None:
    state = _run(decision(found=False, reject_reason=reason.value, notes="nope"))

    assert state.accepted is False
    assert state.reject_reason is reason
    assert state.start_line is None and state.end_line is None


def test_unknown_reject_reason_is_not_trusted_as_a_category() -> None:
    state = _run(decision(found=False, reject_reason="the vibes were off"))

    assert state.reject_reason is RejectReason.AMBIGUOUS_BOUNDARIES


def test_claiming_a_find_without_line_numbers_is_a_rejection() -> None:
    """A schema cannot catch this; the normaliser must."""
    state = _run(decision(found=True, confidence="high", notes="I found it, trust me"))

    assert state.accepted is False
    assert state.reject_reason is RejectReason.AMBIGUOUS_BOUNDARIES


def test_half_a_range_is_a_rejection() -> None:
    state = _run(decision(found=True, start_line=14, notes="only the start"))

    assert state.reject_reason is RejectReason.AMBIGUOUS_BOUNDARIES


def test_inverted_range_is_a_rejection() -> None:
    state = _run(decision(found=True, start_line=90, end_line=12))

    assert state.reject_reason is RejectReason.AMBIGUOUS_BOUNDARIES
    assert "inverted" in state.notes


def test_unparseable_confidence_degrades_to_low() -> None:
    state = _run(decision(found=True, start_line=1, end_line=2, confidence="extremely sure"))

    assert state.confidence is Confidence.LOW


def test_a_model_that_only_talks_never_decides() -> None:
    """Text replies with no tool call must not be mistaken for a decision."""
    state = _run(["I think the story starts somewhere around line 14."])

    assert state.reject_reason is RejectReason.BUDGET_EXHAUSTED


def test_rejection_carries_no_line_numbers_even_if_the_model_sent_them() -> None:
    state = _run(decision(found=False, reject_reason="no_story_found", start_line=5, end_line=9))

    assert state.start_line is None
    assert state.end_line is None


def test_empty_book_is_rejected_not_crashed() -> None:
    state = _run(decision(found=False, reject_reason="no_story_found"), body=_body(0))

    assert state.accepted is False
    assert state.iterations_used == 0
