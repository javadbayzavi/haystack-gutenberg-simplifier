"""PCM framing."""

import io
import wave

import pytest

from gutenberg_narrator.audio import AudioFormat, silence, wav_header, wav_stream_header


def test_exact_header_produces_a_file_the_stdlib_can_read() -> None:
    fmt = AudioFormat()
    pcm = silence(fmt, 0.25)

    with wave.open(io.BytesIO(wav_header(fmt, len(pcm)) + pcm)) as parsed:
        assert parsed.getframerate() == 24_000
        assert parsed.getnchannels() == 1
        assert parsed.getsampwidth() == 2
        assert parsed.getnframes() == 6_000


def test_stream_header_declares_an_unknown_length() -> None:
    """Decoders read to EOF rather than trusting the count."""
    header = wav_stream_header(AudioFormat())

    assert len(header) == 44
    assert header[4:8] == b"\xff\xff\xff\xff"  # RIFF size
    assert header[40:44] == b"\xff\xff\xff\xff"  # data size


def test_byte_counts_land_on_whole_frames() -> None:
    """A part-frame offset swaps stereo channels for the rest of the stream."""
    fmt = AudioFormat(sample_rate=24_000, channels=2)

    for seconds in (0.0001, 0.333, 1.7, 9.99):
        assert fmt.bytes_for(seconds) % fmt.block_align == 0


def test_duration_round_trips() -> None:
    fmt = AudioFormat()

    assert fmt.seconds_for(fmt.bytes_for(2.5)) == pytest.approx(2.5, abs=1e-4)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"sample_rate": 0}, "sample_rate must be positive"),
        ({"channels": 3}, "channels must be 1 or 2"),
        ({"sample_width_bytes": 3}, "sample_width_bytes must be"),
    ],
)
def test_impossible_formats_are_rejected(kwargs: dict[str, int], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        AudioFormat(**kwargs)


def test_negative_durations_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        silence(AudioFormat(), -1.0)


def test_zero_length_data_is_a_valid_file() -> None:
    fmt = AudioFormat()

    with wave.open(io.BytesIO(wav_header(fmt, 0))) as parsed:
        assert parsed.getnframes() == 0
