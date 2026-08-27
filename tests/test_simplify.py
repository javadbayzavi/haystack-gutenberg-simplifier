"""Segmentation, continuity and usage accounting. No model, no network."""

import pytest

from gutenberg_simplifier.boundaries import BoundaryState, Confidence, RejectReason
from gutenberg_simplifier.models import BookBody
from gutenberg_simplifier.results import Usage
from gutenberg_simplifier.simplify import (
    segment_story,
    simplify_story,
    story_lines,
)
from gutenberg_simplifier.tiers import AgeTier
from tests.stubs import StubChatGenerator

BODY_START = 10


def _body(lines: tuple[str, ...]) -> BookBody:
    return BookBody(book_id=1, lines=lines, start_line=BODY_START, markers_found=True)


def _accepted(start: int, end: int) -> BoundaryState:
    return BoundaryState(
        start_line=start,
        end_line=end,
        reject_reason=None,
        confidence=Confidence.HIGH,
        notes="",
        iterations_used=1,
    )


def test_story_lines_uses_absolute_numbers() -> None:
    body = _body(tuple(f"line {i}" for i in range(20)))

    # Raw lines 12..14 are body indices 2..4, and end_line is inclusive.
    assert story_lines(body, _accepted(12, 14)) == ("line 2", "line 3", "line 4")


def test_story_lines_clamps_out_of_range_numbers() -> None:
    """The model's numbers are clamped, not trusted."""
    body = _body(tuple(f"line {i}" for i in range(5)))

    assert story_lines(body, _accepted(0, 9_999)) == body.lines


def test_story_lines_is_empty_for_an_unusable_range() -> None:
    body = _body(tuple(f"line {i}" for i in range(5)))

    assert story_lines(body, _accepted(100, 200)) == ()


def test_story_lines_is_empty_for_a_rejection() -> None:
    body = _body(("a", "b"))
    rejected = BoundaryState.rejected(RejectReason.NO_STORY_FOUND, notes="", iterations_used=1)

    assert story_lines(body, rejected) == ()


def test_segments_break_at_paragraphs_not_mid_sentence() -> None:
    lines = ("para one a", "para one b", "", "para two a", "para two b", "")
    segments = segment_story(lines, max_lines=3)

    assert len(segments) == 2
    assert segments[0] == ("para one a", "para one b", "")
    assert segments[1] == ("para two a", "para two b", "")


def test_an_oversized_paragraph_is_kept_whole() -> None:
    """Better one big segment than one severed one."""
    lines = tuple(f"line {i}" for i in range(50))
    segments = segment_story(lines, max_lines=10)

    assert len(segments) == 1
    assert segments[0] == lines


def test_every_line_survives_segmentation() -> None:
    lines = tuple(f"line {i}" if i % 4 else "" for i in range(60))
    segments = segment_story(lines, max_lines=7)

    rejoined = tuple(line for segment in segments for line in segment)
    assert rejoined == lines


def test_empty_story_yields_no_segments() -> None:
    assert segment_story(()) == []


def test_continuity_tail_is_passed_to_the_next_segment() -> None:
    """Names and tense have to survive a seam."""
    body = _body(tuple(f"line {i}" if i % 3 else "" for i in range(30)))
    generator = StubChatGenerator("The rabbit hopped away.")

    simplify_story(
        body, _accepted(BODY_START, BODY_START + 29), AgeTier.PRESCHOOL, generator, max_lines=6
    )

    assert len(generator.seen) > 1
    first_user = generator.seen[0][1].text or ""
    second_user = generator.seen[1][1].text or ""
    assert "previously" not in first_user
    assert "The rabbit hopped away." in second_user


def test_segments_are_numbered_for_the_model() -> None:
    body = _body(tuple(f"line {i}" if i % 3 else "" for i in range(30)))
    generator = StubChatGenerator("out")

    story = simplify_story(
        body, _accepted(BODY_START, BODY_START + 29), AgeTier.PRESCHOOL, generator, max_lines=6
    )

    assert f"of {story.segments}" in (generator.seen[0][1].text or "")


def test_an_unusable_range_costs_no_model_calls() -> None:
    generator = StubChatGenerator("out")
    story = simplify_story(_body(("a",)), _accepted(500, 600), AgeTier.PRESCHOOL, generator)

    assert story.segments == 0
    assert story.usage.calls == 0
    assert generator.seen == []


def test_usage_adds_across_calls() -> None:
    assert Usage(10, 5, 1) + Usage(3, 2, 1) == Usage(13, 7, 2)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-5", round((1_000_000 * 5.0 + 1_000_000 * 25.0) / 1_000_000, 6)),
    ],
)
def test_cost_is_computed_from_the_price_table(model: str, expected: float) -> None:
    assert Usage(1_000_000, 1_000_000, 2).cost_usd(model) == expected


def test_unknown_model_cost_is_none_not_zero() -> None:
    """A silent zero reads as 'free', which is the opposite of 'unknown'."""
    assert Usage(100, 100, 1).cost_usd("some-other-model") is None
