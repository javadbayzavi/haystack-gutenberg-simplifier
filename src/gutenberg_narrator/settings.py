"""Runtime configuration, all from the environment.

Engine selection lives here rather than in the app so that adding a real engine
is a change to one function, and so a deployment can switch engines without a
code change.
"""

import os

from gutenberg_narrator.engine import SynthesisEngine
from gutenberg_narrator.narrate import DEFAULT_MAX_TOTAL_CHARACTERS
from gutenberg_narrator.segmentation import DEFAULT_MAX_CHARACTERS
from gutenberg_narrator.stub import StubEngine

#: The stub is the default on purpose. A deployment that forgets to choose an
#: engine produces silence rather than an unexpected bill or a missing-model
#: crash loop, and readiness still reports which engine is in use.
DEFAULT_ENGINE = "stub"

_ENGINES = ("stub", "hosted")


def engine_name() -> str:
    return os.environ.get("NARRATOR_ENGINE", DEFAULT_ENGINE).strip().lower()


def build_engine(name: str | None = None) -> SynthesisEngine:
    """Construct the configured engine.

    Raises:
        ValueError: the configured engine is not one this build knows about.
            Raised at startup rather than on the first request, so a typo fails
            the deployment instead of every request.
    """
    chosen = (name or engine_name()).strip().lower()
    if chosen == "stub":
        return StubEngine()
    if chosen == "hosted":
        from gutenberg_narrator.hosted import (
            DEFAULT_BASE_URL,
            DEFAULT_MODEL,
            DEFAULT_VOICE_NAMES,
            HostedEngine,
        )

        key = tts_api_key()
        if not key:
            # Fail the deployment, not every request. A pod that starts without
            # its credential and then 500s on traffic is worse than one that
            # never starts.
            raise ValueError("NARRATOR_ENGINE=hosted requires TTS_API_KEY")

        return HostedEngine(
            key,
            base_url=os.environ.get("TTS_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("TTS_MODEL", DEFAULT_MODEL),
            voice_names=_voice_name_overrides() or dict(DEFAULT_VOICE_NAMES),
        )
    raise ValueError(f"Unknown NARRATOR_ENGINE {chosen!r}. Available: {', '.join(_ENGINES)}")


def tts_api_key() -> str | None:
    """Credential for the hosted engine. Absent when the stub is in use."""
    return os.environ.get("TTS_API_KEY") or None


def _voice_name_overrides() -> dict[str, str]:
    """Per-tier provider voice names from TTS_VOICE_<TIER>.

    Overridable because the right name for a children's narrator differs
    between providers, and a redeploy should not need a code change.
    """
    overrides: dict[str, str] = {}
    for tier in ("preschool", "early_reader", "middle_grade"):
        value = os.environ.get(f"TTS_VOICE_{tier.upper()}")
        if value:
            overrides[tier] = value
    return overrides


def api_token() -> str | None:
    """Bearer token, or None when authentication is disabled."""
    return os.environ.get("NARRATOR_API_TOKEN") or None


def max_unit_characters() -> int:
    return int(os.environ.get("NARRATOR_MAX_UNIT_CHARACTERS", DEFAULT_MAX_CHARACTERS))


def max_total_characters() -> int:
    return int(os.environ.get("NARRATOR_MAX_TOTAL_CHARACTERS", DEFAULT_MAX_TOTAL_CHARACTERS))
