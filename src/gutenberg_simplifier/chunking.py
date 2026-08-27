"""Deterministic chunked reading over a book body.

This module is the reason the agent loop is safe. The model decides *what* a
chunk means; it never decides whether to keep going. The reader alone advances
the cursor, counts iterations, and refuses to serve more once the budget is
spent -- so termination is a property of code that has no model in it, and stays
true no matter how the model misbehaves.

Chunks overlap so a boundary marker straddling a chunk edge is still seen whole
by at least one turn. Every chunk reports absolute line numbers in the *raw*
download, not offsets into the stripped body, so a decision the agent returns
can be applied to the original text without further translation.
"""

import math
from dataclasses import dataclass

from gutenberg_simplifier.models import BookBody

#: Big enough to hold a scene, small enough that a wrong guess is cheap.
DEFAULT_CHUNK_LINES = 60

#: Carried into the next chunk so a marker split across an edge is seen intact.
DEFAULT_OVERLAP_LINES = 5

#: Safety net, not the cost control -- the size budget in `fetch` is what keeps
#: a novel out. Sized to cover a children's book comfortably: at the default
#: stride this is ~2100 lines, against ~220 for a picture book. A body that
#: needs more iterations than this is refused as BUDGET_EXHAUSTED rather than
#: quietly costing a hundred model turns.
DEFAULT_MAX_ITERATIONS = 40


@dataclass(frozen=True, slots=True)
class Chunk:
    """A window over the body, addressed in raw-text line numbers."""

    index: int
    start_line: int
    end_line: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class ChunkReader:
    """Serves a body one overlapping window at a time, under a fixed budget.

    Not a Haystack component: the agent needs to drive this directly from a
    tool, and the simplification stage reuses it outside any pipeline run.
    """

    def __init__(
        self,
        body: BookBody,
        *,
        chunk_lines: int = DEFAULT_CHUNK_LINES,
        overlap_lines: int = DEFAULT_OVERLAP_LINES,
        max_iterations: int | None = None,
    ) -> None:
        if chunk_lines <= 0:
            raise ValueError(f"chunk_lines must be positive, got {chunk_lines}")
        if overlap_lines < 0:
            raise ValueError(f"overlap_lines must not be negative, got {overlap_lines}")
        if overlap_lines >= chunk_lines:
            # Otherwise the stride is zero or negative and the cursor never moves.
            raise ValueError(
                f"overlap_lines ({overlap_lines}) must be less than chunk_lines ({chunk_lines})"
            )

        self._body = body
        self._chunk_lines = chunk_lines
        self._overlap_lines = overlap_lines
        self._cursor = 0
        self._iterations = 0
        self._max_iterations = (
            max_iterations if max_iterations is not None else DEFAULT_MAX_ITERATIONS
        )

    @property
    def stride(self) -> int:
        """Lines advanced per read; the overlap is re-shown, not skipped."""
        return self._chunk_lines - self._overlap_lines

    @property
    def chunks_available(self) -> int:
        """How many reads it would take to cover the body, ignoring the budget."""
        remaining = self._body.line_count
        if remaining == 0:
            return 0
        return math.ceil(remaining / self.stride)

    @property
    def iterations_used(self) -> int:
        return self._iterations

    @property
    def max_iterations(self) -> int:
        return self._max_iterations

    @property
    def budget_exhausted(self) -> bool:
        """True once the iteration budget is spent, whatever is left unread."""
        return self._iterations >= self._max_iterations

    @property
    def at_eof(self) -> bool:
        return self._cursor >= self._body.line_count

    @property
    def finished(self) -> bool:
        return self.at_eof or self.budget_exhausted

    def read_next(self) -> Chunk | None:
        """Return the next window, or ``None`` when finished.

        ``None`` means *stop asking*: either the body is consumed or the budget
        is spent. The caller cannot tell the two apart from here on purpose --
        both mean there is nothing further to read.
        """
        if self.finished:
            return None

        start = self._cursor
        end = min(start + self._chunk_lines, self._body.line_count)
        chunk = Chunk(
            index=self._iterations,
            start_line=self._body.raw_line_number(start),
            end_line=self._body.raw_line_number(end - 1) + 1,
            lines=self._body.lines[start:end],
        )

        self._iterations += 1
        self._cursor += self.stride
        return chunk
