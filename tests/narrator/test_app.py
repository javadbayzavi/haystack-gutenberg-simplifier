"""The narrator HTTP surface."""

import io
import wave

import pytest
from fastapi.testclient import TestClient

from gutenberg_narrator.app import MAX_BODY_BYTES, REQUEST_ID_HEADER, create_application
from gutenberg_narrator.engine import VoiceSettings
from gutenberg_narrator.errors import EngineError
from gutenberg_narrator.stub import StubEngine

TEXT = "Once upon a time there was a rabbit. He lived under a hedge. He ate a carrot."


@pytest.fixture
def engine() -> StubEngine:
    return StubEngine()


@pytest.fixture
def client(engine: StubEngine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("NARRATOR_API_TOKEN", raising=False)
    return TestClient(create_application(engine))


@pytest.fixture
def authed_client(engine: StubEngine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("NARRATOR_API_TOKEN", "s3cret")
    return TestClient(create_application(engine))


def test_narration_returns_playable_audio(client: TestClient) -> None:
    response = client.post("/narrate", json={"text": TEXT, "voice": "preschool"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")
    assert len(response.content) > 44  # more than a bare header


def test_the_stream_is_a_wav_of_unknown_length(client: TestClient) -> None:
    """A stream cannot know its total size when the header goes out."""
    body = client.post("/narrate", json={"text": TEXT}).content

    assert body[4:8] == b"\xff\xff\xff\xff"
    assert body[40:44] == b"\xff\xff\xff\xff"


def test_a_buffered_client_can_rewrite_the_header_and_play_it(client: TestClient) -> None:
    """Documents the recovery path for a client that wants a seekable file."""
    from gutenberg_narrator.audio import AudioFormat, wav_header

    body = client.post("/narrate", json={"text": TEXT}).content
    pcm = body[44:]

    with wave.open(io.BytesIO(wav_header(AudioFormat(), len(pcm)) + pcm)) as parsed:
        assert parsed.getnframes() > 0


def test_headers_announce_what_was_promised(client: TestClient) -> None:
    """The only way a client can notice a truncated stream."""
    response = client.post("/narrate", json={"text": TEXT, "voice": "middle_grade"})

    assert int(response.headers["X-Narration-Units"]) >= 1
    assert response.headers["X-Narration-Voice"] == "middle_grade"
    assert response.headers["X-Narration-Sample-Rate"] == "24000"


def test_the_requested_voice_reaches_the_engine(client: TestClient, engine: StubEngine) -> None:
    client.post("/narrate", json={"text": TEXT, "voice": "preschool"})

    voices: list[VoiceSettings] = [voice for _, voice in engine.spoken]
    assert voices and all(voice.name == "preschool" for voice in voices)


def test_an_unknown_voice_is_refused_before_synthesis(
    client: TestClient, engine: StubEngine
) -> None:
    response = client.post("/narrate", json={"text": TEXT, "voice": "pirate"})

    assert response.status_code == 400
    assert "pirate" in response.json()["detail"]
    assert engine.spoken == []


def test_oversized_text_is_refused_with_a_status_code(
    client: TestClient, engine: StubEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusable things must be refused before the stream takes status codes away."""
    monkeypatch.setenv("NARRATOR_MAX_TOTAL_CHARACTERS", "50")

    response = client.post("/narrate", json={"text": "x" * 200})

    assert response.status_code == 413
    assert engine.spoken == []


def test_text_with_nothing_to_say_is_a_400_not_an_empty_stream(
    client: TestClient, engine: StubEngine
) -> None:
    response = client.post("/narrate", json={"text": "   ..   "})

    assert response.status_code == 400
    assert engine.spoken == []


def test_missing_text_fails_schema_validation(client: TestClient) -> None:
    assert client.post("/narrate", json={"voice": "preschool"}).status_code == 422


def test_a_mid_stream_engine_failure_truncates_rather_than_corrupting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The response is already 200 and the body is audio, so there is nowhere to
    put an explanation. What must not happen is a corrupt or hanging response."""
    monkeypatch.delenv("NARRATOR_API_TOKEN", raising=False)

    class FailsOnSecond(StubEngine):
        def synthesise(self, text: str, voice: VoiceSettings) -> bytes:
            if len(self.spoken) >= 1:
                raise EngineError("provider exploded")
            return super().synthesise(text, voice)

    client = TestClient(create_application(FailsOnSecond()))
    response = client.post("/narrate", json={"text": TEXT, "voice": "preschool"})

    assert response.status_code == 200
    assert response.content.startswith(b"RIFF")
    assert len(response.content) > 44  # the first unit still arrived


def test_voices_are_listed(client: TestClient) -> None:
    names = {voice["name"] for voice in client.get("/voices").json()}

    assert names == {"preschool", "early_reader", "middle_grade"}


def test_liveness_needs_no_configuration(client: TestClient) -> None:
    assert client.get("/health/live").json()["status"] == "alive"


def test_readiness_reports_the_engine(client: TestClient) -> None:
    payload = client.get("/health/ready").json()

    assert payload["status"] == "ready"
    assert payload["checks"]["engine_loaded"] is True


def test_auth_is_off_unless_a_token_is_configured(client: TestClient) -> None:
    assert client.get("/voices").status_code == 200


def test_a_configured_token_is_required(authed_client: TestClient) -> None:
    assert authed_client.get("/voices").status_code == 401
    assert authed_client.post("/narrate", json={"text": TEXT}).status_code == 401


def test_the_right_token_is_accepted(authed_client: TestClient) -> None:
    response = authed_client.get("/voices", headers={"Authorization": "Bearer s3cret"})

    assert response.status_code == 200


def test_health_stays_reachable_without_a_token(authed_client: TestClient) -> None:
    assert authed_client.get("/health/live").status_code == 200
    assert authed_client.get("/health/ready").status_code == 200


def test_metrics_is_not_public(authed_client: TestClient) -> None:
    assert authed_client.get("/metrics").status_code == 401


def test_metrics_render(client: TestClient) -> None:
    client.post("/narrate", json={"text": TEXT})

    body = client.get("/metrics").text
    assert "narrate_requests_total" in body
    assert "narrate_realtime_factor" in body


def test_an_incoming_request_id_is_preserved(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "caller-id"})

    assert response.headers[REQUEST_ID_HEADER] == "caller-id"


def test_an_oversized_body_is_rejected_early(client: TestClient) -> None:
    response = client.post(
        "/narrate",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_the_engine_is_closed_on_shutdown() -> None:
    """A hosted engine holds a connection pool; a local one holds weights.
    Neither should outlive the process quietly."""

    class ClosableStub(StubEngine):
        closed = False

        def close(self) -> None:
            type(self).closed = True

    engine = ClosableStub()
    with TestClient(create_application(engine)):
        pass  # entering and leaving runs the lifespan

    assert ClosableStub.closed is True


def test_readiness_flags_a_hosted_engine_with_no_credential(
    monkeypatch: pytest.MonkeyPatch, engine: StubEngine
) -> None:
    """Readiness, not liveness: no restart can supply a secret."""
    monkeypatch.setenv("NARRATOR_ENGINE", "hosted")
    monkeypatch.delenv("TTS_API_KEY", raising=False)
    monkeypatch.delenv("NARRATOR_API_TOKEN", raising=False)

    payload = TestClient(create_application(engine)).get("/health/ready").json()

    assert payload["status"] == "not_ready"
    assert payload["checks"]["credential_present"] is False
