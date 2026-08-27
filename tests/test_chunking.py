"""Reader tests. Entirely deterministic -- no model, no network.

The safety property the agent depends on is proved here, not in the agent
tests: this reader terminates under every input, so the loop above it does too.
"""

import pytest

from gutenberg_simplifier.chunking import Chunk, ChunkReader
from gutenberg_simplifier.models import BookBody


def _body(line_count: int, *, start_line: int = 10) -> BookBody:
    return BookBody(
        book_id=1,
        lines=tuple(f"line {i}" for i in range(line_count)),
        start_line=start_line,
        markers_found=True,
    )


def _read_all(reader: ChunkReader) -> list[Chunk]:
    chunks: list[Chunk] = []
    # Bounded independently of the reader, so a broken reader fails the test
    # instead of hanging the suite.
    for _ in range(1000):
        chunk = reader.read_next()
        if chunk is None:
            return chunks
        chunks.append(chunk)
    raise AssertionError("reader did not terminate within 1000 reads")


def test_chunks_overlap_by_the_configured_amount() -> None:
    reader = ChunkReader(_body(30), chunk_lines=10, overlap_lines=3)
    chunks = _read_all(reader)

    # Second chunk restarts 3 lines before the first one ended.
    assert chunks[0].lines[-3:] == chunks[1].lines[:3]


def test_line_numbers_are_absolute_in_the_raw_text() -> None:
    """Offsets must be usable against the original download, not the body."""
    reader = ChunkReader(_body(30, start_line=10), chunk_lines=10, overlap_lines=0)
    chunks = _read_all(reader)

    assert chunks[0].start_line == 10
    assert chunks[0].end_line == 20
    assert chunks[1].start_line == 20


def test_every_line_of_the_body_is_covered() -> None:
    body = _body(97)
    seen: set[str] = set()
    for chunk in _read_all(ChunkReader(body, chunk_lines=10, overlap_lines=3)):
        seen.update(chunk.lines)

    assert seen == set(body.lines)


def test_final_chunk_is_truncated_not_padded() -> None:
    chunks = _read_all(ChunkReader(_body(25), chunk_lines=10, overlap_lines=0))

    assert len(chunks[-1].lines) == 5
    assert chunks[-1].lines[-1] == "line 24"


def test_reader_stops_at_eof() -> None:
    reader = ChunkReader(_body(25), chunk_lines=10, overlap_lines=0)
    _read_all(reader)

    assert reader.at_eof is True
    assert reader.read_next() is None


def test_budget_stops_the_reader_before_eof() -> None:
    """The whole point: the budget binds even with body left unread."""
    reader = ChunkReader(_body(1000), chunk_lines=10, overlap_lines=0, max_iterations=3)
    chunks = _read_all(reader)

    assert len(chunks) == 3
    assert reader.budget_exhausted is True
    assert reader.at_eof is False  # there was plenty left
    assert reader.read_next() is None


def test_reader_keeps_returning_none_once_finished() -> None:
    """A caller that ignores None must not be able to restart the reader."""
    reader = ChunkReader(_body(10), chunk_lines=10, overlap_lines=0, max_iterations=1)
    _read_all(reader)

    assert [reader.read_next() for _ in range(5)] == [None] * 5
    assert reader.iterations_used == 1


def test_empty_body_yields_nothing() -> None:
    reader = ChunkReader(_body(0), chunk_lines=10, overlap_lines=0)

    assert _read_all(reader) == []
    assert reader.chunks_available == 0


@pytest.mark.parametrize("line_count", [1, 9, 10, 11, 59, 60, 61, 218, 3380])
def test_reader_always_terminates(line_count: int) -> None:
    """Including the real measured sizes: Peter Rabbit 218, Alice 3380."""
    reader = ChunkReader(_body(line_count), max_iterations=1000)
    chunks = _read_all(reader)

    assert reader.finished is True
    assert len(chunks) == reader.chunks_available


def test_stride_of_zero_is_rejected_at_construction() -> None:
    """A non-advancing cursor is an infinite loop; refuse it up front."""
    with pytest.raises(ValueError, match="must be less than"):
        ChunkReader(_body(10), chunk_lines=10, overlap_lines=10)


def test_negative_stride_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be less than"):
        ChunkReader(_body(10), chunk_lines=5, overlap_lines=9)


@pytest.mark.parametrize(
    ("chunk_lines", "overlap_lines", "match"),
    [(0, 0, "chunk_lines must be positive"), (10, -1, "must not be negative")],
)
def test_invalid_geometry_is_rejected(chunk_lines: int, overlap_lines: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ChunkReader(_body(10), chunk_lines=chunk_lines, overlap_lines=overlap_lines)


def test_measured_book_sizes_fit_the_default_budget() -> None:
    """Peter Rabbit and Alice, as measured by the CLI."""
    peter_rabbit = ChunkReader(_body(218))
    alice = ChunkReader(_body(3380))

    assert peter_rabbit.chunks_available <= peter_rabbit.max_iterations
    # Alice is novel-length and deliberately does NOT fit: it is rejected as
    # BUDGET_EXHAUSTED rather than silently costing 60+ model turns.
    assert alice.chunks_available > alice.max_iterations
