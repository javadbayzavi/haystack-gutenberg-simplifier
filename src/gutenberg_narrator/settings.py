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

_ENGINES = ("stub",)


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
    raise ValueError(f"Unknown NARRATOR_ENGINE {chosen!r}. Available: {', '.join(_ENGINES)}")


def api_token() -> str | None:
    """Bearer token, or None when authentication is disabled."""
    return os.environ.get("NARRATOR_API_TOKEN") or None


def max_unit_characters() -> int:
    return int(os.environ.get("NARRATOR_MAX_UNIT_CHARACTERS", DEFAULT_MAX_CHARACTERS))


def max_total_characters() -> int:
    return int(os.environ.get("NARRATOR_MAX_TOTAL_CHARACTERS", DEFAULT_MAX_TOTAL_CHARACTERS))
