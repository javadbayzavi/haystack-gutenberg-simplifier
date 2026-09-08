"""Splitting prose for synthesis."""

import pytest

from gutenberg_narrator.segmentation import split_for_synthesis, split_sentences


def test_sentences_keep_their_terminal_punctuation() -> None:
    assert split_sentences("He ran. She stopped! Why?") == ["He ran.", "She stopped!", "Why?"]


@pytest.mark.parametrize(
    "text",
    [
        "Mr. Rabbit ate a carrot. Then he left.",
        "Dr. Smith arrived. He was late.",
        "She saw etc. written there. It confused her.",
    ],
)
def test_abbreviations_do_not_end_a_sentence(text: str) -> None:
    """A false break mid-sentence is audible: the first half gets a falling
    final intonation it should not have."""
    assert len(split_sentences(text)) == 2


def test_a_quote_closing_a_sentence_stays_with_it() -> None:
    sentences = split_sentences('"Stop!" she cried. He did not stop.')

    assert len(sentences) == 2
    assert sentences[0].endswith("cried.")


def test_whole_sentences_are_packed_together_where_they_fit() -> None:
    text = "One two. Three four. Five six."

    assert split_for_synthesis(text, max_characters=100) == ["One two. Three four. Five six."]


def test_packing_respects_the_budget() -> None:
    text = " ".join(f"Sentence number {i} here." for i in range(40))

    units = split_for_synthesis(text, max_characters=80)

    assert len(units) > 1
    assert all(len(unit) <= 80 for unit in units)


def test_a_sentence_is_never_split_when_it_fits() -> None:
    text = "A short one. " + "x" * 60 + ". Another."

    for unit in split_for_synthesis(text, max_characters=100):
        # No unit may begin mid-sentence with a lowercase run-on fragment.
        assert unit[0].isupper() or unit[0] == "x"


def test_an_oversized_sentence_breaks_at_punctuation_not_mid_phrase() -> None:
    sentence = (
        "The rabbit ran through the garden; he passed the shed, "
        "the water butt, and the old apple tree; then he stopped."
    )

    units = split_for_synthesis(sentence, max_characters=60)

    assert all(len(unit) <= 60 for unit in units)
    # The strongest available mark is kept with the clause it closes.
    assert any(unit.endswith(";") for unit in units)


def test_a_sentence_with_no_punctuation_falls_back_to_words() -> None:
    sentence = " ".join(["word"] * 60) + "."

    units = split_for_synthesis(sentence, max_characters=50)

    assert all(len(unit) <= 50 for unit in units)
    assert not any("wor d" in unit for unit in units)


def test_a_single_word_longer_than_the_budget_is_not_cut() -> None:
    """Cutting mid-word makes the engine pronounce two nonsense fragments."""
    monster = "a" * 120

    units = split_for_synthesis(f"{monster}.", max_characters=50)

    assert units == [f"{monster}."]


def test_every_word_survives_segmentation() -> None:
    text = "One two three. Four five six; seven eight. Nine ten!"

    rejoined = " ".join(split_for_synthesis(text, max_characters=20))

    for word in ("One", "five", "seven", "ten!"):
        assert word in rejoined


def test_empty_and_whitespace_yield_nothing() -> None:
    assert split_for_synthesis("") == []
    assert split_for_synthesis("   \n\t ") == []


def test_an_impossible_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        split_for_synthesis("text", max_characters=0)


@pytest.mark.parametrize("text", ["...", "   ..   ", "— — —", "***", "  ~~~  "])
def test_units_with_nothing_speakable_are_dropped(text: str) -> None:
    """A page ornament costs an engine call and returns an artefact, not silence."""
    assert split_for_synthesis(text) == []


def test_a_bare_number_is_still_speakable() -> None:
    assert split_for_synthesis("1892.") == ["1892."]


def test_ornaments_between_real_sentences_do_not_survive() -> None:
    units = split_for_synthesis("He ran. * * * She stopped.", max_characters=20)

    assert all(any(c.isalnum() for c in unit) for unit in units)
