"""An engine that produces silence.

Not only a test double: this is the engine developers and CI run. It means the
HTTP surface, segmentation, pacing and audio framing can all be exercised with
no model, no GPU, no API key and no network, and it means a contributor working
on the service never downloads a gigabyte of weights.

Silence is generated in proportion to the text, at roughly a natural speaking
rate, so durations and byte counts are realistic enough to test streaming
behaviour and pacing against.
"""

from gutenberg_narrator.audio import AudioFormat, silence
from gutenberg_narrator.engine import VoiceSettings

#: Roughly a measured English narration rate for children's fiction.
CHARACTERS_PER_SECOND = 14.0


class StubEngine:
    """Emits silence sized as though the text had been spoken."""

    def __init__(self, audio_format: AudioFormat | None = None) -> None:
        self._audio_format = audio_format or AudioFormat()
        #: Every passage handed to the engine, so tests can assert what was sent.
        self.spoken: list[tuple[str, VoiceSettings]] = []

    @property
    def audio_format(self) -> AudioFormat:
        return self._audio_format

    def synthesise(self, text: str, voice: VoiceSettings) -> bytes:
        self.spoken.append((text, voice))
        seconds = len(text) / (CHARACTERS_PER_SECOND * voice.pace)
        return silence(self._audio_format, seconds)

    def close(self) -> None:
        """Nothing to release."""
        return
