"""Tiered simplification over the range the boundary agent resolved.

A whole book does not fit comfortably in one response, so the story is split and
rewritten segment by segment. Two things make that safe rather than merely
possible:

*Segments break at paragraphs.* Cutting mid-paragraph hands the model half a
scene and gets half a rewrite. Paragraphs are the smallest unit that survives
being rewritten alone.

*Segments carry continuity.* Each call receives the tail of the previous
*rewritten* output, so names and tense stay put across a seam. That makes the
loop sequential and therefore slower; parallelising it is PR 9's problem, and
would need a different continuity mechanism than "what you just wrote".
"""

from dataclasses import dataclass

from haystack.core.component import Component
from haystack.dataclasses import ChatMessage

from gutenberg_simplifier.boundaries import BoundaryState
from gutenberg_simplifier.models import BookBody
from gutenberg_simplifier.prompts import (
    CONTINUITY_HINT,
    SEGMENT_USER,
    TIERED_SIMPLIFY_SYSTEM,
)
from gutenberg_simplifier.results import Usage
from gutenberg_simplifier.tiers import AgeTier, guidance_for

#: Lines of source per model call. Roughly a chapter of a picture book.
DEFAULT_SEGMENT_LINES = 120

#: How much of the previous rewrite to show for continuity.
CONTINUITY_TAIL_CHARS = 400


def build_system_message(tier: AgeTier) -> ChatMessage:
    """The instruction the model works under for every segment of a book.

    Shared with the streaming path. Both used to assemble this independently,
    which is the shape that quietly diverges: a change to the tier prompt would
    have reached the JSON endpoint and not the chat one, and nothing would have
    failed.
    """
    guidance = guidance_for(tier)
    return ChatMessage.from_system(
        TIERED_SIMPLIFY_SYSTEM.format(age_range=guidance.age_range, guidance=guidance.guidance)
    )


def build_segment_message(
    segment: tuple[str, ...], *, index: int, total: int, previous: str
) -> ChatMessage:
    """One segment's request, carrying continuity from the previous rewrite.

    ``previous`` is the previously *rewritten* text, not the source: what has to
    stay consistent across a seam is the prose the reader just saw.
    """
    return ChatMessage.from_user(
        SEGMENT_USER.format(
            part=index + 1,
            total=total,
            continuity=(
                CONTINUITY_HINT.format(tail=previous[-CONTINUITY_TAIL_CHARS:]) if previous else ""
            ),
            passage="\n".join(segment),
        )
    )


@dataclass(frozen=True, slots=True)
class SimplifiedStory:
    text: str
    usage: Usage
    segments: int


def story_lines(body: BookBody, boundaries: BoundaryState) -> tuple[str, ...]:
    """Extract the story from the body using absolute line numbers.

    The model's numbers are clamped to the body rather than trusted. An
    out-of-range line is not an exception here -- it yields a shorter range, and
    an empty result is the caller's signal that the boundaries were unusable.
    """
    if boundaries.start_line is None or boundaries.end_line is None:
        return ()

    start = max(boundaries.start_line - body.start_line, 0)
    # end_line names the story's last line, so the slice runs one past it.
    end = min(boundaries.end_line - body.start_line + 1, body.line_count)
    if start >= end:
        return ()
    return body.lines[start:end]


def segment_story(
    lines: tuple[str, ...], *, max_lines: int = DEFAULT_SEGMENT_LINES
) -> list[tuple[str, ...]]:
    """Split into segments of at most ``max_lines``, breaking at paragraphs.

    A single paragraph longer than ``max_lines`` is emitted whole rather than
    cut: an oversized segment is a smaller problem than a severed one.
    """
    if not lines:
        return []

    segments: list[tuple[str, ...]] = []
    current: list[str] = []

    for paragraph in _paragraphs(lines):
        if current and len(current) + len(paragraph) > max_lines:
            segments.append(tuple(current))
            current = []
        current.extend(paragraph)

    if current:
        segments.append(tuple(current))
    return segments


def _paragraphs(lines: tuple[str, ...]) -> list[list[str]]:
    """Group lines into paragraphs, keeping the blank separators attached."""
    paragraphs: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if not line.strip() and any(existing.strip() for existing in current):
            paragraphs.append(current)
            current = []

    if current:
        paragraphs.append(current)
    return paragraphs


def simplify_story(
    body: BookBody,
    boundaries: BoundaryState,
    tier: AgeTier,
    chat_generator: Component,
    *,
    max_lines: int = DEFAULT_SEGMENT_LINES,
) -> SimplifiedStory:
    """Rewrite the resolved story for ``tier``, one segment at a time."""
    segments = segment_story(story_lines(body, boundaries), max_lines=max_lines)
    if not segments:
        return SimplifiedStory(text="", usage=Usage(), segments=0)

    system = build_system_message(tier)
    parts: list[str] = []
    usage = Usage()

    for index, segment in enumerate(segments):
        user = build_segment_message(
            segment,
            index=index,
            total=len(segments),
            previous=parts[-1] if parts else "",
        )
        result = chat_generator.run(messages=[system, user])
        replies = result.get("replies") or []
        usage = usage + _usage_from(replies)

        text = replies[0].text if replies else None
        if text:
            parts.append(text.strip())

    return SimplifiedStory(
        text="\n\n".join(parts),
        usage=usage,
        segments=len(segments),
    )


#: Providers and integrations disagree on these key names, so read all of them
#: rather than silently accounting zero tokens for an unfamiliar spelling.
_INPUT_KEYS = ("prompt_tokens", "input_tokens")
_OUTPUT_KEYS = ("completion_tokens", "output_tokens")


def _usage_from(replies: list[ChatMessage]) -> Usage:
    """Sum token usage across replies, tolerating an absent or odd shape."""
    total = Usage(calls=1)
    for reply in replies:
        raw = (reply.meta or {}).get("usage") or {}
        if not isinstance(raw, dict):
            continue
        total = Usage(
            input_tokens=total.input_tokens + _first_int(raw, _INPUT_KEYS),
            output_tokens=total.output_tokens + _first_int(raw, _OUTPUT_KEYS),
            calls=total.calls,
        )
    return total


def _first_int(source: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int):
            return value
    return 0
