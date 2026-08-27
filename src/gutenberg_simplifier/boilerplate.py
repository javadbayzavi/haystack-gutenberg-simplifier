"""Removal of Project Gutenberg's licence header and footer.

Gutenberg wraps every plain-text edition in a licence block. Those blocks are
long, formulaic, and would otherwise dominate the first chunks the
boundary-detection agent reads -- so stripping them here is both a correctness
fix and a cost saving.

The stripping is deliberately *tolerant*: markers have drifted across decades of
Gutenberg's own tooling ("THE" vs "THIS", "EBOOK" vs "ETEXT"), and older texts
may carry neither. Missing markers are reported, never raised -- a book with no
markers is still a book, and the agent downstream can cope with a little extra
front matter.
"""

import re

from gutenberg_simplifier.models import BookBody, RawBook

_START_MARKER = re.compile(
    r"^\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG (?:EBOOK|ETEXT)\b.*$",
    re.IGNORECASE,
)
_END_MARKER = re.compile(
    r"^\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG (?:EBOOK|ETEXT)\b.*$",
    re.IGNORECASE,
)


def strip_gutenberg_boilerplate(book: RawBook) -> BookBody:
    """Return the book's content with the licence header and footer removed.

    The returned :class:`BookBody` records where its first line sat in the raw
    text, so downstream line numbers stay expressible against the original
    download.
    """
    lines = book.lines

    start_idx = _find_first(lines, _START_MARKER)
    body_start = start_idx + 1 if start_idx is not None else 0

    end_idx = _find_last(lines, _END_MARKER, after=body_start)
    body_end = end_idx if end_idx is not None else len(lines)

    body_start, body_end = _trim_blank_lines(lines, body_start, body_end)

    return BookBody(
        book_id=book.book_id,
        lines=lines[body_start:body_end],
        start_line=body_start,
        markers_found=start_idx is not None and end_idx is not None,
    )


def _find_first(lines: tuple[str, ...], pattern: re.Pattern[str]) -> int | None:
    for index, line in enumerate(lines):
        if pattern.match(line.strip()):
            return index
    return None


def _find_last(lines: tuple[str, ...], pattern: re.Pattern[str], *, after: int) -> int | None:
    for index in range(len(lines) - 1, after - 1, -1):
        if pattern.match(lines[index].strip()):
            return index
    return None


def _trim_blank_lines(lines: tuple[str, ...], start: int, end: int) -> tuple[int, int]:
    """Narrow ``[start, end)`` past blank lines at either edge."""
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return start, end
