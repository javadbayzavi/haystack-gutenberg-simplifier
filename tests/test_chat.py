"""Chat parsing. Pure functions, no I/O -- and the place chat surfaces break."""

import pytest

from gutenberg_simplifier.chat import ChatParseError, parse_chat_request
from gutenberg_simplifier.tiers import AgeTier


@pytest.mark.parametrize(
    "text",
    [
        "14838",
        "simplify 14838",
        "please simplify book 14838",
        "  14838  ",
        "https://www.gutenberg.org/ebooks/14838 please",
    ],
)
def test_finds_the_book_id(text: str) -> None:
    assert parse_chat_request(text).book_id == 14838


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14838 for a preschooler", AgeTier.PRESCHOOL),
        ("14838 preschool", AgeTier.PRESCHOOL),
        ("14838 early reader", AgeTier.EARLY_READER),
        ("14838 early_reader", AgeTier.EARLY_READER),
        ("14838 middle grade", AgeTier.MIDDLE_GRADE),
        ("14838 middle-grade", AgeTier.MIDDLE_GRADE),
    ],
)
def test_tier_keywords(text: str, expected: AgeTier) -> None:
    assert parse_chat_request(text).tier is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14838 for a 4 year old", AgeTier.PRESCHOOL),
        ("14838 for a 7 year old", AgeTier.EARLY_READER),
        ("14838 for a 10 year old", AgeTier.MIDDLE_GRADE),
        ("14838 age 5", AgeTier.PRESCHOOL),
        ("14838 aged 9", AgeTier.MIDDLE_GRADE),
        ("14838 for my 6yo", AgeTier.EARLY_READER),
        ("14838 for a 8 yr old", AgeTier.EARLY_READER),
    ],
)
def test_ages_map_to_tiers(text: str, expected: AgeTier) -> None:
    assert parse_chat_request(text).tier is expected


@pytest.mark.parametrize(
    "text",
    [
        "14838 for a 5 year old",
        "simplify 14838 for my 7 year old",
        "book 14838, age 4 please",
        "read 14838 to a 3yo",
    ],
)
def test_an_age_is_never_mistaken_for_the_book_id(text: str) -> None:
    """The failure that silently fetches the wrong book."""
    assert parse_chat_request(text).book_id == 14838


def test_explicit_id_wins_over_a_leading_number() -> None:
    assert parse_chat_request("I have 2 kids, book 14838 please").book_id == 14838


def test_a_low_book_id_still_parses_when_labelled() -> None:
    """Gutenberg ids overlap the age range; the label disambiguates."""
    assert parse_chat_request("book 11 for a 5 year old").book_id == 11


def test_tier_keyword_beats_a_conflicting_age() -> None:
    request = parse_chat_request("14838 middle grade for a 4 year old")

    assert request.tier is AgeTier.MIDDLE_GRADE
    assert request.book_id == 14838


def test_defaults_to_early_reader_when_no_age_is_given() -> None:
    assert parse_chat_request("14838").tier is AgeTier.EARLY_READER


def test_default_tier_is_configurable() -> None:
    request = parse_chat_request("14838", default_tier=AgeTier.MIDDLE_GRADE)

    assert request.tier is AgeTier.MIDDLE_GRADE


@pytest.mark.parametrize("text", ["", "   ", None, "please simplify a nice story"])
def test_messages_without_an_id_are_refused_with_help(text: str | None) -> None:
    with pytest.raises(ChatParseError) as caught:
        parse_chat_request(text)

    # The message is streamed straight to a person, so it must read like one.
    assert "14838" in str(caught.value)


def test_zero_is_not_a_book_id() -> None:
    with pytest.raises(ChatParseError):
        parse_chat_request("book 0")
