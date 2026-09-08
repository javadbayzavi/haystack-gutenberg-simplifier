"""A synthesis engine backed by an OpenAI-compatible speech API.

Chosen for the shape of the deployment rather than the quality of the voice.
This service is otherwise identical whether it calls an API or loads a model,
and an engine that runs anywhere means the pipeline around it -- segmentation,
pacing, streaming, framing -- can be exercised for real without a GPU. Swapping
in a self-hosted model later is a change to this file and to resource limits,
not to the architecture.

``/v1/audio/speech`` is spoken by several providers, so the base URL is
configuration rather than a hard-coded vendor.

Requests ask for raw PCM, not MP3 or WAV. Every alternative would have to be
decoded before it could be concatenated with the next unit's audio, and a
per-unit container would put file headers in the middle of the stream -- the
exact failure the audio module exists to avoid.
"""

import logging

import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from gutenberg_narrator.audio import AudioFormat
from gutenberg_narrator.engine import VoiceSettings
from gutenberg_narrator.errors import EngineError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini-tts"

#: The API returns 24 kHz 16-bit mono for `response_format: pcm`, which is what
#: AudioFormat defaults to. A mismatch here would play at the wrong speed rather
#: than fail, so it is stated explicitly instead of assumed.
PCM_FORMAT = AudioFormat(sample_rate=24_000, channels=1, sample_width_bytes=2)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3

#: Provider voices per reading age. Overridable, because the good name for a
#: children's narrator differs between providers.
DEFAULT_VOICE_NAMES: dict[str, str] = {
    "preschool": "nova",
    "early_reader": "fable",
    "middle_grade": "alloy",
}
FALLBACK_VOICE_NAME = "alloy"

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class _TransientError(Exception):
    """Internal marker: this attempt failed in a way worth retrying."""


class HostedEngine:
    """Calls a hosted speech API and returns raw PCM."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        voice_names: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_multiplier: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for the hosted engine")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._voice_names = voice_names or dict(DEFAULT_VOICE_NAMES)
        self._max_attempts = max_attempts
        self._backoff_multiplier = backoff_multiplier
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def audio_format(self) -> AudioFormat:
        return PCM_FORMAT

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def synthesise(self, text: str, voice: VoiceSettings) -> bytes:
        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice_names.get(voice.name, FALLBACK_VOICE_NAME),
            "response_format": "pcm",
            # The provider's own rate control, so the engine does no resampling.
            "speed": round(voice.pace, 2),
        }

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=self._backoff_multiplier, max=10),
                retry=retry_if_exception_type(_TransientError),
                reraise=False,
            ):
                with attempt:
                    return self._request(payload)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            raise EngineError(str(last) or "no successful attempt") from exc

        raise EngineError("retry loop ended without a result")

    def _request(self, payload: dict[str, object]) -> bytes:
        try:
            response = self._client.post(
                f"{self._base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise _TransientError(f"{type(exc).__name__}: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise _TransientError(f"HTTP {response.status_code}")
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # A 4xx is a permanent statement about this request. Retrying burns
            # the budget and changes nothing.
            raise EngineError(f"HTTP {response.status_code}: {_detail(response)}")

        audio = response.content
        if not audio:
            raise EngineError("provider returned an empty body")
        if len(audio) % PCM_FORMAT.block_align:
            # A partial frame shifts every following sample; better to refuse
            # than to emit audio that drifts.
            raise EngineError(f"provider returned {len(audio)} bytes, not a whole number of frames")
        return audio


def _detail(response: httpx.Response) -> str:
    """A short, safe description of a failure. Never the whole body."""
    try:
        message = response.json().get("error", {}).get("message")
    except Exception:  # noqa: BLE001 - any parse failure falls back to the status
        message = None
    return str(message)[:200] if message else response.reason_phrase
