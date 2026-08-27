"""Value objects passed between the deterministic stages.

Both are frozen: once fetched, the raw text is the fixed ground truth that every
later line index refers back to.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RawBook:
    """Exactly what Gutenberg served, unmodified."""

    book_id: int
    text: str
    source_url: str
    size_bytes: int

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.splitlines())


@dataclass(frozen=True, slots=True)
class BookBody:
    """The book with Gutenberg's licence boilerplate removed.

    ``start_line`` is the offset of ``lines[0]`` within the *raw* text, so any
    index derived from this body can be translated back to a position in the
    original download. The boundary-detection agent reports line numbers
    against the raw text, and this offset is what makes that possible.
    """

    book_id: int
    lines: tuple[str, ...]
    start_line: int
    markers_found: bool

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def raw_line_number(self, body_index: int) -> int:
        """Translate an index into this body back to a raw-text line number."""
        if not 0 <= body_index < len(self.lines):
            raise IndexError(f"body index {body_index} outside 0..{len(self.lines) - 1}")
        return self.start_line + body_index
