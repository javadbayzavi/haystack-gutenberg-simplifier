"""PCM audio framing.

WAV is awkward to stream: its header declares the total byte count, which a
stream does not know when the first byte goes out. Three ways to handle that,
and this module supports the two that are honest:

*Streaming header.* Write the length fields as ``0xFFFFFFFF`` and append frames
until the connection closes. ffmpeg, VLC and most decoders read to EOF and are
fine. Browsers are less reliable, and the resulting file is not seekable.

*Exact header.* Once the whole thing is buffered, the length is known and the
header is correct. Seekable, playable everywhere, but nothing can play until
synthesis finishes.

The third option -- concatenating complete WAV files -- is the one that looks
easiest and is wrong: the result has 44-byte headers embedded mid-stream, which
some decoders reject and others silently render as a click.
"""

import struct
from dataclasses import dataclass

#: Sentinel length for a stream of unknown size.
_UNKNOWN_LENGTH = 0xFFFFFFFF

_PCM_FORMAT_TAG = 1
_HEADER_BYTES = 44


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """Uncompressed PCM parameters.

    Engines declare their own output format; the assembler refuses to mix two,
    because concatenating differing sample rates produces audio that plays at
    the wrong speed rather than failing loudly.
    """

    sample_rate: int = 24_000
    channels: int = 1
    sample_width_bytes: int = 2

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {self.sample_rate}")
        if self.channels not in (1, 2):
            raise ValueError(f"channels must be 1 or 2, got {self.channels}")
        if self.sample_width_bytes not in (1, 2, 4):
            raise ValueError(f"sample_width_bytes must be 1, 2 or 4, got {self.sample_width_bytes}")

    @property
    def bits_per_sample(self) -> int:
        return self.sample_width_bytes * 8

    @property
    def block_align(self) -> int:
        return self.channels * self.sample_width_bytes

    @property
    def byte_rate(self) -> int:
        return self.sample_rate * self.block_align

    def bytes_for(self, seconds: float) -> int:
        """PCM bytes for a duration, rounded to a whole frame.

        Rounding to the frame matters: a byte count that is not a multiple of
        block_align shifts every following sample by part of a frame, which on
        stereo swaps the channels for the rest of the stream.
        """
        frames = round(seconds * self.sample_rate)
        return frames * self.block_align

    def seconds_for(self, byte_count: int) -> float:
        return byte_count / self.byte_rate


def wav_header(fmt: AudioFormat, data_bytes: int | None = None) -> bytes:
    """A 44-byte RIFF/WAVE header.

    ``data_bytes`` of ``None`` writes the streaming sentinel; a real count
    writes an exact, seekable header.
    """
    if data_bytes is None:
        riff_size = _UNKNOWN_LENGTH
        data_size = _UNKNOWN_LENGTH
    else:
        if data_bytes < 0:
            raise ValueError(f"data_bytes must not be negative, got {data_bytes}")
        data_size = data_bytes
        riff_size = data_bytes + _HEADER_BYTES - 8

    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        _PCM_FORMAT_TAG,
        fmt.channels,
        fmt.sample_rate,
        fmt.byte_rate,
        fmt.block_align,
        fmt.bits_per_sample,
        b"data",
        data_size,
    )


def wav_stream_header(fmt: AudioFormat) -> bytes:
    """The header to send before a stream of unknown length."""
    return wav_header(fmt, None)


def silence(fmt: AudioFormat, seconds: float) -> bytes:
    """Silent PCM. Used for pauses between segments and by the stub engine."""
    if seconds < 0:
        raise ValueError(f"seconds must not be negative, got {seconds}")
    return b"\x00" * fmt.bytes_for(seconds)
