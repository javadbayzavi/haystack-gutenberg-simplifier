"""Voice profiles by reading age.

The names deliberately match the simplifier's tiers, but by *convention* rather
than by import. A shared enum would make the narrator depend on the service it
is an optional add-on to, and an add-on that cannot be deployed without its host
is not optional. If the two ever drift, narration still works: the voice is just
a string, and an unrecognised one is a clear error rather than a crash.

Younger listeners get a slower pace, more expression and a longer beat between
paragraphs -- all three help a child track a story they cannot yet read.
"""

from gutenberg_narrator.engine import VoiceSettings
from gutenberg_narrator.errors import UnknownVoiceError

VOICE_PROFILES: dict[str, VoiceSettings] = {
    "preschool": VoiceSettings(name="preschool", pace=0.85, expressiveness=0.85, pause_seconds=1.0),
    "early_reader": VoiceSettings(
        name="early_reader", pace=0.95, expressiveness=0.65, pause_seconds=0.7
    ),
    "middle_grade": VoiceSettings(
        name="middle_grade", pace=1.0, expressiveness=0.5, pause_seconds=0.5
    ),
}

DEFAULT_VOICE = "early_reader"


def voice_for(name: str | None) -> VoiceSettings:
    """Look up a profile by name, falling back to the default for ``None``."""
    if name is None:
        return VOICE_PROFILES[DEFAULT_VOICE]

    profile = VOICE_PROFILES.get(name.strip().lower())
    if profile is None:
        raise UnknownVoiceError(name, tuple(sorted(VOICE_PROFILES)))
    return profile
