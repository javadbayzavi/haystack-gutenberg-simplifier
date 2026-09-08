"""Voice profiles."""

import pytest

from gutenberg_narrator.engine import VoiceSettings
from gutenberg_narrator.errors import UnknownVoiceError
from gutenberg_narrator.voices import DEFAULT_VOICE, VOICE_PROFILES, voice_for


def test_younger_listeners_get_a_slower_pace_and_longer_pauses() -> None:
    """The whole point of having profiles rather than one voice."""
    preschool = voice_for("preschool")
    middle = voice_for("middle_grade")

    assert preschool.pace < middle.pace
    assert preschool.pause_seconds > middle.pause_seconds
    assert preschool.expressiveness > middle.expressiveness


@pytest.mark.parametrize("name", ["preschool", "EARLY_READER", "  middle_grade  "])
def test_lookup_is_forgiving_about_case_and_spacing(name: str) -> None:
    assert voice_for(name).name == name.strip().lower()


def test_none_falls_back_to_the_default() -> None:
    assert voice_for(None) is VOICE_PROFILES[DEFAULT_VOICE]


def test_an_unknown_voice_names_the_alternatives() -> None:
    with pytest.raises(UnknownVoiceError) as caught:
        voice_for("gravelly_pirate")

    assert "gravelly_pirate" in str(caught.value)
    assert "preschool" in str(caught.value)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"pace": 0.0}, "pace must be"),
        ({"pace": 10.0}, "pace must be"),
        ({"expressiveness": 1.5}, "expressiveness must be"),
        ({"pause_seconds": -1}, "must not be negative"),
    ],
)
def test_impossible_settings_are_rejected(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        VoiceSettings(name="x", **kwargs)


def test_profile_names_match_their_keys() -> None:
    """A mismatch would make the name in a response disagree with the request."""
    assert all(key == profile.name for key, profile in VOICE_PROFILES.items())
