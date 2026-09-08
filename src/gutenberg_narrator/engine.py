"""The synthesis boundary.

Everything above this line -- segmentation, pacing, audio framing, the HTTP
surface -- is independent of which engine actually speaks. That is the point:
it keeps the choice between a hosted API and a self-hosted model an
implementation detail rather than an architecture, and it lets the whole
service be built and tested with no model, no GPU and no API key.

``synthesise`` is synchronous. A self-hosted model is CPU- or GPU-bound, where
async buys nothing, and a hosted engine's blocking call is cheap to run in a
worker thread. A sync protocol that both can satisfy is simpler than an async
one that a local model would have to fake.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gutenberg_narrator.audio import AudioFormat


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """How a passage should sound.

    Expressed as intent rather than engine parameters, because every engine
    spells these differently and the mapping belongs in the engine.
    """

    name: str
    #: 1.0 is the engine's natural rate; below 1.0 is slower.
    pace: float = 1.0
    #: 0.0 flat, 1.0 highly animated.
    expressiveness: float = 0.5
    #: A beat between segments. Continuous narration with no pause between
    #: paragraphs is noticeably harder for a child to follow.
    pause_seconds: float = 0.6

    def __post_init__(self) -> None:
        if not 0.25 <= self.pace <= 4.0:
            raise ValueError(f"pace must be between 0.25 and 4.0, got {self.pace}")
        if not 0.0 <= self.expressiveness <= 1.0:
            raise ValueError(
                f"expressiveness must be between 0.0 and 1.0, got {self.expressiveness}"
            )
        if self.pause_seconds < 0:
            raise ValueError(f"pause_seconds must not be negative, got {self.pause_seconds}")


@runtime_checkable
class SynthesisEngine(Protocol):
    """Text in, PCM out."""

    @property
    def audio_format(self) -> AudioFormat:
        """The format every call to :meth:`synthesise` returns."""
        ...

    def synthesise(self, text: str, voice: VoiceSettings) -> bytes:
        """Render one passage as raw PCM in :attr:`audio_format`.

        Raises:
            EngineError: synthesis failed for a reason the caller cannot fix.
        """
        ...

    def close(self) -> None:
        """Release whatever the engine holds.

        Part of the contract even though a stub has nothing to release: an
        engine with a connection pool or a loaded model needs a shutdown hook,
        and the service cannot call one that only some implementations have.
        """
        ...
