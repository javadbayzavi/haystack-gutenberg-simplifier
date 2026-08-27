"""Turning a chat message into a simplification request.

The OpenAI-compatible endpoint receives free text, so something has to decide
that "read me 14838 for a 5 year old" means book 14838 at the preschool tier.
That parsing is the most fragile part of any chat surface, so it lives here as
a pure function with no I/O and is tested against the phrasings people actually
type -- including the ones that should be refused.

The hard case is that a message can contain two numbers, a book id and an age,
and picking wrong silently fetches the wrong book. Ages are therefore consumed
by explicit patterns first, and whatever survives is the id.
"""

import re
from dataclasses import dataclass

from gutenberg_simplifier.tiers import AgeTier

#: Ages a tier can plausibly be requested by.
_AGE_TO_TIER: dict[int, AgeTier] = {
    3: AgeTier.PRESCHOOL,
    4: AgeTier.PRESCHOOL,
    5: AgeTier.PRESCHOOL,
    6: AgeTier.EARLY_READER,
    7: AgeTier.EARLY_READER,
    8: AgeTier.EARLY_READER,
    9: AgeTier.MIDDLE_GRADE,
    10: AgeTier.MIDDLE_GRADE,
    11: AgeTier.MIDDLE_GRADE,
}

_TIER_KEYWORDS: list[tuple[re.Pattern[str], AgeTier]] = [
    (re.compile(r"\bpre[\s-]?school(er)?\b", re.IGNORECASE), AgeTier.PRESCHOOL),
    (re.compile(r"\bearly[\s_-]?reader\b", re.IGNORECASE), AgeTier.EARLY_READER),
    (re.compile(r"\bmiddle[\s_-]?grade\b", re.IGNORECASE), AgeTier.MIDDLE_GRADE),
]

#: "for a 5 year old", "age 7", "aged 9", "5yo".
_AGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(\d{1,2})\s*(?:year|yr)s?[\s-]*old\b", re.IGNORECASE),
    re.compile(r"\bage[ds]?\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*y\.?o\.?\b", re.IGNORECASE),
]

#: An explicit id always wins over positional guessing.
_EXPLICIT_ID = re.compile(
    r"\b(?:book|id|gutenberg)\s*(?:id|#|number)?\s*[:#]?\s*(\d+)\b", re.IGNORECASE
)

_ANY_NUMBER = re.compile(r"\b(\d+)\b")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    book_id: int
    tier: AgeTier


class ChatParseError(ValueError):
    """The message did not name a book. Carries text meant for the user."""


def parse_chat_request(
    text: str | None, *, default_tier: AgeTier = AgeTier.EARLY_READER
) -> ChatRequest:
    """Extract a book id and reading tier from a chat message.

    Raises:
        ChatParseError: no book id could be identified. The message is written
            for a person, since it is streamed straight back to them.
    """
    if not text or not text.strip():
        raise ChatParseError("Tell me which book to simplify, for example: 14838 for a 5 year old.")

    remaining = text
    tier: AgeTier | None = None

    for pattern, keyword_tier in _TIER_KEYWORDS:
        if pattern.search(remaining):
            tier = keyword_tier
            remaining = pattern.sub(" ", remaining)
            break

    # Consume ages even when a keyword already set the tier, so the age's digits
    # can never be mistaken for the book id.
    for pattern in _AGE_PATTERNS:
        match = pattern.search(remaining)
        if match is None:
            continue
        age = int(match.group(1))
        if tier is None:
            tier = _AGE_TO_TIER.get(age)
        remaining = pattern.sub(" ", remaining)
        break

    book_id = _find_book_id(text, remaining)
    if book_id is None:
        raise ChatParseError(
            "I could not find a Project Gutenberg book id in that. "
            "Try a number, for example: 14838 for a 5 year old."
        )

    return ChatRequest(book_id=book_id, tier=tier or default_tier)


def _find_book_id(original: str, remaining: str) -> int | None:
    """Prefer an explicitly labelled id; otherwise take the first number left.

    ``original`` is searched for the explicit form because the age-stripping
    above may have removed surrounding words.
    """
    explicit = _EXPLICIT_ID.search(original)
    if explicit:
        return _positive(explicit.group(1))

    match = _ANY_NUMBER.search(remaining)
    return _positive(match.group(1)) if match else None


def _positive(raw: str) -> int | None:
    value = int(raw)
    return value if value > 0 else None
