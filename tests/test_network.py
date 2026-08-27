"""One test that actually talks to Project Gutenberg.

Deselected by default (``addopts = -m 'not network'``) so the suite stays fast
and offline. Run it deliberately with ``make test-network`` when you want to
confirm the real site still serves what we expect.
"""

import pytest

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.fetch import fetch_book

# "The Tale of Peter Rabbit" -- small, public domain, and the running example
# for the rest of the project.
PETER_RABBIT_ID = 14838


@pytest.mark.network
def test_fetches_and_strips_a_real_book() -> None:
    raw = fetch_book(PETER_RABBIT_ID)
    body = strip_gutenberg_boilerplate(raw)

    assert raw.size_bytes > 0
    assert body.markers_found is True
    assert body.line_count > 0
    assert "PROJECT GUTENBERG" not in body.text.upper()[:500]


@pytest.mark.network
def test_boundary_agent_finds_the_story_in_a_real_book() -> None:
    """The full agent loop against a real book and a real model.

    Needs ANTHROPIC_API_KEY. Deselected by default like the rest of this file.
    """
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from gutenberg_simplifier.boundaries import detect_boundaries
    from gutenberg_simplifier.pipeline import default_generator

    raw = fetch_book(PETER_RABBIT_ID)
    body = strip_gutenberg_boilerplate(raw)

    state = detect_boundaries(body, default_generator())

    assert state.accepted is True, f"rejected: {state.reject_reason} - {state.notes}"
    assert state.start_line is not None and state.end_line is not None
    # Boundaries must land inside the body they were derived from.
    assert body.start_line <= state.start_line < state.end_line
    assert state.end_line <= body.start_line + body.line_count
    # The model reports positions, not prose.
    assert len(state.notes) < 600
