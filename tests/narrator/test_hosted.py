"""The hosted engine, driven entirely through httpx's MockTransport.

No network and no key: every case below is a scripted conversation with a fake
provider, the same approach the simplifier's fetcher uses.
"""

import httpx
import pytest

from gutenberg_narrator.audio import AudioFormat
from gutenberg_narrator.errors import EngineError
from gutenberg_narrator.hosted import PCM_FORMAT, HostedEngine
from gutenberg_narrator.voices import voice_for

# A whole number of frames, as the provider is required to return.
PCM = b"\x00\x01" * 240


def _engine(handler: httpx.MockTransport, **kwargs: object) -> HostedEngine:
    return HostedEngine(
        "test-key",
        client=httpx.Client(transport=handler),
        backoff_multiplier=0,
        **kwargs,  # type: ignore[arg-type]
    )


def _serving(
    content: bytes = PCM, *, status: int = 200, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handle)


def test_returns_the_pcm_the_provider_sent() -> None:
    assert _engine(_serving()).synthesise("Hello.", voice_for(None)) == PCM


def test_the_declared_format_matches_what_is_requested() -> None:
    """A mismatch would play at the wrong speed rather than fail loudly."""
    seen: list[httpx.Request] = []
    _engine(_serving(seen=seen)).synthesise("Hello.", voice_for(None))

    import json

    payload = json.loads(seen[0].content)
    assert payload["response_format"] == "pcm"
    assert AudioFormat(sample_rate=24_000, channels=1, sample_width_bytes=2) == PCM_FORMAT


def test_the_voice_and_pace_reach_the_provider() -> None:
    import json

    seen: list[httpx.Request] = []
    _engine(_serving(seen=seen)).synthesise("Hello.", voice_for("preschool"))

    payload = json.loads(seen[0].content)
    assert payload["voice"] == "nova"
    assert payload["speed"] == pytest.approx(0.85)


def test_an_unmapped_voice_falls_back_rather_than_failing() -> None:
    import json

    from gutenberg_narrator.engine import VoiceSettings

    seen: list[httpx.Request] = []
    _engine(_serving(seen=seen)).synthesise("Hi.", VoiceSettings(name="bespoke"))

    assert json.loads(seen[0].content)["voice"] == "alloy"


def test_voice_names_are_overridable() -> None:
    import json

    seen: list[httpx.Request] = []
    engine = _engine(_serving(seen=seen), voice_names={"preschool": "shimmer"})
    engine.synthesise("Hi.", voice_for("preschool"))

    assert json.loads(seen[0].content)["voice"] == "shimmer"


def test_the_api_key_is_sent_as_a_bearer_token() -> None:
    seen: list[httpx.Request] = []
    _engine(_serving(seen=seen)).synthesise("Hi.", voice_for(None))

    assert seen[0].headers["authorization"] == "Bearer test-key"


def test_a_transient_failure_is_retried_then_succeeds() -> None:
    attempts = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=PCM)

    assert _engine(httpx.MockTransport(handle)).synthesise("Hi.", voice_for(None)) == PCM
    assert attempts["n"] == 2


def test_a_client_error_is_not_retried() -> None:
    """A 4xx is a permanent statement about this request."""
    seen: list[httpx.Request] = []

    with pytest.raises(EngineError, match="400"):
        _engine(_serving(b"", status=400, seen=seen)).synthesise("Hi.", voice_for(None))

    assert len(seen) == 1


def test_a_persistent_server_error_fails_after_the_budget() -> None:
    seen: list[httpx.Request] = []

    with pytest.raises(EngineError):
        _engine(_serving(b"", status=503, seen=seen), max_attempts=3).synthesise(
            "Hi.", voice_for(None)
        )

    assert len(seen) == 3


def test_a_network_error_becomes_an_engine_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(EngineError):
        _engine(httpx.MockTransport(handle), max_attempts=2).synthesise("Hi.", voice_for(None))


def test_an_empty_body_is_refused() -> None:
    with pytest.raises(EngineError, match="empty body"):
        _engine(_serving(b"")).synthesise("Hi.", voice_for(None))


def test_a_partial_frame_is_refused_rather_than_played() -> None:
    """An odd byte count shifts every following sample, so the audio drifts."""
    with pytest.raises(EngineError, match="whole number of frames"):
        _engine(_serving(b"\x00\x01\x02")).synthesise("Hi.", voice_for(None))


def test_provider_error_text_is_surfaced_but_bounded() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "x" * 5000}})

    with pytest.raises(EngineError) as caught:
        _engine(httpx.MockTransport(handle)).synthesise("Hi.", voice_for(None))

    assert len(str(caught.value)) < 400  # not the whole body


def test_construction_without_a_key_is_refused() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        HostedEngine("")
