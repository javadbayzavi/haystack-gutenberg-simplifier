"""Optional composition: the simplifier's narration path.

The property that matters most is the negative one -- with narration
unconfigured, nothing about this service changes.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from gutenberg_simplifier import narration
from gutenberg_simplifier.app import create_application


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUTENBERG_API_TOKEN", raising=False)


def test_narration_is_off_unless_a_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARRATOR_URL", raising=False)

    assert narration.is_enabled() is False
    assert narration.narrator_url() is None


def test_a_trailing_slash_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise the proxied URL grows a double slash."""
    monkeypatch.setenv("NARRATOR_URL", "http://narrator:1417/")

    assert narration.narrator_url() == "http://narrator:1417"


def test_an_empty_url_counts_as_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable and one set to "" mean the same thing in a chart."""
    monkeypatch.setenv("NARRATOR_URL", "")

    assert narration.is_enabled() is False


def test_the_endpoint_reports_not_implemented_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """501, not 404: the route exists and the request is fine -- this
    deployment simply has no narrator."""
    monkeypatch.delenv("NARRATOR_URL", raising=False)

    response = TestClient(create_application()).post("/simplify/narrate", json={"book_id": 14838})

    assert response.status_code == 501
    assert "NARRATOR_URL" in response.json()["detail"]


def test_nothing_else_changes_when_narration_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole claim of an optional add-on, asserted rather than assumed."""
    monkeypatch.delenv("NARRATOR_URL", raising=False)
    client = TestClient(create_application())

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code in (200, 503)
    assert "simplify_requests_total" in client.get("/metrics").text


@pytest.mark.asyncio
async def test_audio_is_proxied_through_without_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NARRATOR_URL", "http://narrator:1417")
    monkeypatch.delenv("NARRATOR_API_TOKEN", raising=False)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/narrate"
        return httpx.Response(200, content=b"RIFF" + b"\x00" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    chunks = [
        chunk async for chunk in narration.stream_narration("Hello.", "preschool", client=client)
    ]
    await client.aclose()

    assert b"".join(chunks).startswith(b"RIFF")


@pytest.mark.asyncio
async def test_the_narrator_token_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATOR_URL", "http://narrator:1417")
    monkeypatch.setenv("NARRATOR_API_TOKEN", "shared-token")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"RIFF")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    [chunk async for chunk in narration.stream_narration("Hi.", "preschool", client=client)]
    await client.aclose()

    assert seen[0].headers["authorization"] == "Bearer shared-token"


@pytest.mark.asyncio
async def test_the_text_and_voice_reach_the_narrator(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setenv("NARRATOR_URL", "http://narrator:1417")
    monkeypatch.delenv("NARRATOR_API_TOKEN", raising=False)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"RIFF")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    [
        chunk
        async for chunk in narration.stream_narration(
            "A rabbit ran.", "middle_grade", client=client
        )
    ]
    await client.aclose()

    payload = json.loads(seen[0].content)
    assert payload == {"text": "A rabbit ran.", "voice": "middle_grade"}


@pytest.mark.asyncio
async def test_a_narrator_failure_surfaces_rather_than_yielding_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent audio would look like a very quiet story rather than an outage."""
    monkeypatch.setenv("NARRATOR_URL", "http://narrator:1417")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        [chunk async for chunk in narration.stream_narration("Hi.", "preschool", client=client)]
    await client.aclose()


@pytest.mark.asyncio
async def test_calling_the_proxy_while_disabled_is_a_programming_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NARRATOR_URL", raising=False)

    with pytest.raises(RuntimeError, match="narration disabled"):
        [chunk async for chunk in narration.stream_narration("Hi.", "preschool")]
