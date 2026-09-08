"""Orchestration: prose in, a stream of PCM out.

Audio is yielded per unit rather than assembled and returned, for the same
reason the simplifier streams tokens: a listener should hear the first sentence
while the last one is still being synthesised. Holding a whole book in memory to
return it at the end would also put a hard ceiling on book length.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from gutenberg_narrator.audio import AudioFormat, silence
from gutenberg_narrator.engine import SynthesisEngine, VoiceSettings
from gutenberg_narrator.errors import TextTooLongError
from gutenberg_narrator.segmentation import DEFAULT_MAX_CHARACTERS, split_for_synthesis

#: Around forty minutes of narration. A cheap rejection before any synthesis,
#: the same shape as the simplifier's size gate: refusing costs nothing, while
#: synthesising an unbounded request costs minutes of compute per request.
DEFAULT_MAX_TOTAL_CHARACTERS = 40_000


@dataclass(frozen=True, slots=True)
class NarrationPlan:
    """What a request will cost, known before any audio is produced."""

    units: tuple[str, ...]
    voice: VoiceSettings
    audio_format: AudioFormat

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def characters(self) -> int:
        return sum(len(unit) for unit in self.units)


def plan_narration(
    text: str,
    voice: VoiceSettings,
    engine: SynthesisEngine,
    *,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    max_total_characters: int = DEFAULT_MAX_TOTAL_CHARACTERS,
) -> NarrationPlan:
    """Segment and validate, without synthesising anything.

    Splitting the plan from the execution is what lets the service reject an
    oversized request with a status code, before it has committed to a stream
    and lost the ability to say anything but prose.
    """
    stripped = text.strip()
    if len(stripped) > max_total_characters:
        raise TextTooLongError(len(stripped), max_total_characters)

    return NarrationPlan(
        units=tuple(split_for_synthesis(stripped, max_characters=max_characters)),
        voice=voice,
        audio_format=engine.audio_format,
    )


def narrate(plan: NarrationPlan, engine: SynthesisEngine) -> Iterator[bytes]:
    """Yield PCM for each unit, with a pause between them.

    The pause is emitted *before* each unit after the first rather than after
    each one, so the stream never ends on trailing silence.
    """
    pause = silence(plan.audio_format, plan.voice.pause_seconds)

    for index, unit in enumerate(plan.units):
        if index and pause:
            yield pause
        yield engine.synthesise(unit, plan.voice)
