"""Optional narration, reached over HTTP.

The simplifier does not import the narrator. It calls it, or -- far more often
-- it does not, because narration is off unless ``NARRATOR_URL`` is set. That
is what makes the add-on genuinely optional: the simplifier ships, deploys and
passes its whole test suite with no knowledge that the narrator exists beyond a
URL it may never dial.

Calling it over HTTP rather than importing it is the same decision as running it
as a separate service, expressed in code. A shared library would tie the two
release cycles together; a URL does not.
"""

import os
from collections.abc import AsyncIterator

import httpx

#: Long: a narration streams for as long as its audio.
DEFAULT_TIMEOUT_SECONDS = 600.0


def narrator_url() -> str | None:
    """The narrator's base URL, or None when narration is not configured."""
    return (os.environ.get("NARRATOR_URL") or "").rstrip("/") or None


def narrator_token() -> str | None:
    return os.environ.get("NARRATOR_API_TOKEN") or None


def is_enabled() -> bool:
    return narrator_url() is not None


async def stream_narration(
    text: str,
    voice: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[bytes]:
    """Proxy a narration request, yielding audio as it arrives.

    Streamed straight through rather than buffered: holding a whole book's audio
    in the simplifier to hand it on afterwards would add its memory cost to this
    service and delay the first byte until the last one existed -- undoing the
    reason the narrator streams at all.
    """
    base = narrator_url()
    if base is None:
        raise RuntimeError("stream_narration called with narration disabled")

    headers = {"Content-Type": "application/json"}
    if token := narrator_token():
        headers["Authorization"] = f"Bearer {token}"

    owned = client is None
    http = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        async with http.stream(
            "POST",
            f"{base}/narrate",
            json={"text": text, "voice": voice},
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk
    finally:
        if owned:
            await http.aclose()
