"""Degradation policy: which failures degrade, and which must stand."""

import pytest

from gutenberg_simplifier.boundaries import BoundaryState, Confidence, RejectReason
from gutenberg_simplifier.fallback import apply_boundary_fallback
from gutenberg_simplifier.models import BookBody

BODY = BookBody(
    book_id=1,
    lines=tuple(f"line {i}" for i in range(20)),
    start_line=10,
    markers_found=True,
)


def _rejected(reason: RejectReason) -> BoundaryState:
    return BoundaryState.rejected(reason, notes="original note", iterations_used=7)


@pytest.mark.parametrize(
    "reason", [RejectReason.BUDGET_EXHAUSTED, RejectReason.AMBIGUOUS_BOUNDARIES]
)
def test_process_failures_degrade_to_the_whole_body(reason: RejectReason) -> None:
    """The search did not converge; that says nothing about the book."""
    state = apply_boundary_fallback(BODY, _rejected(reason))

    assert state.accepted is True
    assert state.start_line == 10
    assert state.end_line == 29
    assert state.fallback_applied is True
    assert state.confidence is Confidence.LOW


@pytest.mark.parametrize(
    "reason",
    [
        RejectReason.NO_STORY_FOUND,
        RejectReason.CORRUPTED_TEXT,
        RejectReason.INAPPROPRIATE_CONTENT,
    ],
)
def test_content_refusals_are_never_overridden(reason: RejectReason) -> None:
    """Overriding these ships exactly what the refusal existed to prevent."""
    state = apply_boundary_fallback(BODY, _rejected(reason))

    assert state.accepted is False
    assert state.reject_reason is reason
    assert state.fallback_applied is False


def test_a_degraded_result_says_so_in_its_notes() -> None:
    """A silent fallback makes a guess indistinguishable from a finding."""
    state = apply_boundary_fallback(BODY, _rejected(RejectReason.BUDGET_EXHAUSTED))

    assert "did not converge" in state.notes
    assert "budget_exhausted" in state.notes
    assert "may remain" in state.notes


def test_an_accepted_result_passes_through_untouched() -> None:
    accepted = BoundaryState(
        start_line=14,
        end_line=25,
        reject_reason=None,
        confidence=Confidence.HIGH,
        notes="found it",
        iterations_used=3,
    )

    assert apply_boundary_fallback(BODY, accepted) is accepted


def test_an_empty_body_has_nothing_to_fall_back_to() -> None:
    empty = BookBody(book_id=1, lines=(), start_line=0, markers_found=False)
    state = apply_boundary_fallback(empty, _rejected(RejectReason.BUDGET_EXHAUSTED))

    assert state.accepted is False
    assert state.fallback_applied is False


def test_iterations_are_preserved_so_the_cost_is_still_visible() -> None:
    state = apply_boundary_fallback(BODY, _rejected(RejectReason.BUDGET_EXHAUSTED))

    assert state.iterations_used == 7
