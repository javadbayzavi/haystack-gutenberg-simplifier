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
