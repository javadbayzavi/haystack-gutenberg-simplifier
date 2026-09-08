"""Turning simplified prose into narrated audio.

Deliberately independent of :mod:`gutenberg_simplifier`. The two are separate
services because they scale on completely different signals -- one waits on an
upstream API, the other is compute-bound -- and an add-on that imports its host
is not optional. A test asserts this package imports nothing from the simplifier.
"""

from gutenberg_narrator.audio import AudioFormat, wav_stream_header
from gutenberg_narrator.engine import SynthesisEngine, VoiceSettings
from gutenberg_narrator.errors import (
    EngineError,
    GutenbergNarratorError,
    TextTooLongError,
    UnknownVoiceError,
)
from gutenberg_narrator.segmentation import split_for_synthesis
from gutenberg_narrator.stub import StubEngine
from gutenberg_narrator.voices import VOICE_PROFILES, voice_for

__all__ = [
    "VOICE_PROFILES",
    "AudioFormat",
    "EngineError",
    "GutenbergNarratorError",
    "StubEngine",
    "SynthesisEngine",
    "TextTooLongError",
    "UnknownVoiceError",
    "VoiceSettings",
    "split_for_synthesis",
    "voice_for",
    "wav_stream_header",
]
