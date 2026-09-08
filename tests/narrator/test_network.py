"""One test that talks to a real speech provider.

Deselected by default like the simplifier's, so the suite stays offline. Run it
deliberately with ``make test-network`` once TTS_API_KEY is set.
"""

import os

import pytest

from gutenberg_narrator.audio import AudioFormat
from gutenberg_narrator.settings import build_engine
from gutenberg_narrator.voices import voice_for


@pytest.mark.network
def test_a_real_provider_returns_speakable_pcm() -> None:
    if not os.environ.get("TTS_API_KEY"):
        pytest.skip("TTS_API_KEY not set")

    engine = build_engine("hosted")
    try:
        pcm = engine.synthesise("Once upon a time there was a rabbit.", voice_for("preschool"))
    finally:
        engine.close()

    fmt: AudioFormat = engine.audio_format
    assert len(pcm) % fmt.block_align == 0
    # A short sentence should be a second or two, not a click and not a minute.
    assert 0.5 < fmt.seconds_for(len(pcm)) < 20
    # Not pure silence: a provider returning zeros would pass every other check.
    assert any(byte for byte in pcm[: 4 * fmt.byte_rate])
