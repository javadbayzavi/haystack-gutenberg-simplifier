"""Splitting prose into units a synthesis engine can speak.

This is not the same problem as splitting prose for rewriting, and reusing that
logic would sound wrong. Two differences drive it:

*Sentences are the unit, not paragraphs.* A sentence cut in half is rendered as
two sentences: the first gets a falling final intonation it should not have, and
the second starts cold. The seam is audible in a way a seam between rewritten
paragraphs never was.

*The limit is hard, not advisory.* TTS models degrade or truncate past a few
hundred characters rather than simply costing more, so an oversized unit is a
correctness problem.

So: never split a sentence unless it alone exceeds the budget, and when forced,
break at the strongest punctuation available rather than at an arbitrary offset.
"""

import re

#: Comfortable for current TTS models. Long enough to carry a full sentence,
#: short enough to stay well inside typical input limits.
DEFAULT_MAX_CHARACTERS = 350

#: Abbreviations whose full stop does not end a sentence. Not exhaustive -- a
#: missed one costs a slightly clipped pause, not a failure.
_ABBREVIATIONS = (
    "Mr",
    "Mrs",
    "Ms",
    "Dr",
    "Prof",
    "St",
    "Sr",
    "Jr",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "No",
    "Fig",
)

_SENTENCE_END = re.compile(
    r"""
    (?<=[.!?])        # a sentence-ending mark
    ["')\]]*          # optionally closed by a quote or bracket
    \s+               # then whitespace
    (?=["'(\[]*[A-Z]) # and something that starts like a new sentence
    """,
    re.VERBOSE,
)

#: Descending strength: where to break a sentence that is too long on its own.
#: The em and en dashes are deliberate, not typos for a hyphen -- Gutenberg
#: texts use both as clause separators, and matching only "-" would miss them.
_CLAUSE_BREAKS = (";", ":", "—", "–", ",")  # noqa: RUF001


def split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping terminal punctuation attached."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return []

    protected = collapsed
    for abbreviation in _ABBREVIATIONS:
        protected = protected.replace(f"{abbreviation}. ", f"{abbreviation}\x00 ")

    parts = _SENTENCE_END.split(protected)
    return [restored for part in parts if (restored := part.replace("\x00", ".").strip())]


def split_for_synthesis(text: str, *, max_characters: int = DEFAULT_MAX_CHARACTERS) -> list[str]:
    """Split ``text`` into units of at most ``max_characters``.

    Whole sentences are packed together where they fit. A sentence longer than
    the budget on its own is broken at the strongest punctuation available, and
    only at a word boundary if it has no punctuation at all.
    """
    if max_characters <= 0:
        raise ValueError(f"max_characters must be positive, got {max_characters}")

    units: list[str] = []
    current = ""

    for sentence in split_sentences(text):
        for piece in _fit(sentence, max_characters):
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= max_characters:
                current = f"{current} {piece}"
            else:
                units.append(current)
                current = piece

    if current:
        units.append(current)
    return units


def _fit(sentence: str, max_characters: int) -> list[str]:
    """Break one sentence down until every piece fits."""
    if len(sentence) <= max_characters:
        return [sentence]

    for mark in _CLAUSE_BREAKS:
        if mark in sentence:
            pieces = _split_keeping(sentence, mark, max_characters)
            if pieces is not None:
                return pieces

    return _split_on_words(sentence, max_characters)


def _split_keeping(sentence: str, mark: str, max_characters: int) -> list[str] | None:
    """Pack clauses around ``mark``; None if that alone is not enough."""
    clauses = [clause.strip() for clause in sentence.split(mark) if clause.strip()]
    if len(clauses) < 2:
        return None

    packed: list[str] = []
    current = ""
    for index, clause in enumerate(clauses):
        # Put the mark back, except after the final clause.
        piece = clause if index == len(clauses) - 1 else f"{clause}{mark}"
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_characters:
            current = f"{current} {piece}"
        else:
            packed.append(current)
            current = piece
    if current:
        packed.append(current)

    # If a single clause is still oversized, this mark did not help; the caller
    # tries the next one, and falls back to word splitting.
    if any(len(piece) > max_characters for piece in packed):
        return None
    return packed


def _split_on_words(sentence: str, max_characters: int) -> list[str]:
    """Last resort. A word longer than the budget is emitted oversized rather
    than cut mid-word, which would be pronounced as two nonsense fragments."""
    pieces: list[str] = []
    current = ""
    for word in sentence.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_characters:
            current = f"{current} {word}"
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces
