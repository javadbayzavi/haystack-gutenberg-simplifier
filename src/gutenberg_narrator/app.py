"""The narrator HTTP service.

Audio streams as it is synthesised, for the same reason the simplifier streams
tokens: a listener should hear the first sentence while the last is still being
produced, and nothing should have to hold a whole book in memory.

The failure model differs from the simplifier's in one way worth stating.
Rejections that happen *before* the stream starts -- unknown voice, text over
budget, missing token -- are ordinary status codes, which is why planning is
separated from synthesis. A failure *during* the stream cannot be: the response
is already 200 and the body is audio, so there is no place to put a sentence
explaining it. The stream ends short. That is a real limitation, not a solved
problem: ``X-Narration-Units`` is sent up front so a client can at least know
how many segments were promised, and every truncation is counted and logged.
Full detection would need a container format with an index, which is beyond MVP.
"""

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gutenberg_narrator import metrics, settings
from gutenberg_narrator.audio import wav_stream_header
from gutenberg_narrator.engine import SynthesisEngine
from gutenberg_narrator.errors import EngineError, TextTooLongError, UnknownVoiceError
from gutenberg_narrator.narrate import NarrationPlan, narrate, plan_narration
from gutenberg_narrator.observability import (
    configure_logging,
    new_request_id,
    request_id_var,
)
from gutenberg_narrator.voices import VOICE_PROFILES, voice_for

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

#: Health checks come from a kubelet with no credentials.
_UNAUTHENTICATED_PATHS = frozenset({"/health/live", "/health/ready"})

#: A narration request is a passage of prose. Generous, but not unbounded.
MAX_BODY_BYTES = 1024 * 1024


class NarrateRequest(BaseModel):
    text: str = Field(description="The prose to narrate.", min_length=1)
    voice: str | None = Field(
        default=None,
        description="Voice profile: preschool, early_reader or middle_grade. "
        "Defaults to early_reader.",
    )


class VoiceModel(BaseModel):
    name: str
    pace: float
    expressiveness: float
    pause_seconds: float


def create_application(engine: SynthesisEngine | None = None) -> FastAPI:
    """Build the service. ``engine`` is injected in tests."""
    configure_logging()

    # Built here, not in the lifespan, so a bad NARRATOR_ENGINE or a missing
    # credential fails the deployment rather than every request.
    built = engine if engine is not None else settings.build_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # A hosted engine holds a connection pool and a local one holds
            # model weights; neither should outlive the process quietly.
            app.state.engine.close()

    app = FastAPI(
        title="gutenberg-narrator",
        description="Turns simplified prose into narrated audio.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = built

    _add_middleware(app)
    _add_routes(app)
    return app


def _add_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = request_id_var.set(request_id)
        try:
            rejection = _reject(request)
            if rejection is not None:
                metrics.record_http_error(rejection.status_code)
                response: Response = rejection
            else:
                response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


def _reject(request: Request) -> JSONResponse | None:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413, content={"detail": f"Request body exceeds {MAX_BODY_BYTES} bytes"}
        )

    expected = settings.api_token()
    if expected is None or request.url.path in _UNAUTHENTICATED_PATHS:
        return None

    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not _constant_time_equal(presented, expected):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing bearer token"})
    return None


def _constant_time_equal(presented: str, expected: str) -> bool:
    import hmac

    return hmac.compare_digest(presented.encode(), expected.encode())


def _add_routes(app: FastAPI) -> None:
    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request) -> Response:
        """Ready means an engine is loaded and declares a usable format.

        A real engine loads model weights here; until that has happened the pod
        must not take traffic it would drop.
        """
        engine: SynthesisEngine | None = getattr(request.app.state, "engine", None)
        checks = {"engine_loaded": engine is not None}
        if engine is not None:
            checks["engine_format_valid"] = engine.audio_format.sample_rate > 0
        if settings.engine_name() == "hosted":
            # A missing credential fails readiness, never liveness: no restart
            # can supply one, so failing liveness would only crash-loop.
            checks["credential_present"] = settings.tts_api_key() is not None

        ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "not_ready",
                "engine": settings.engine_name(),
                "checks": checks,
            },
        )

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        payload, content_type = metrics.render()
        return Response(content=payload, media_type=content_type)

    @app.get("/voices")
    async def voices() -> list[VoiceModel]:
        """The available voice profiles."""
        return [
            VoiceModel(
                name=profile.name,
                pace=profile.pace,
                expressiveness=profile.expressiveness,
                pause_seconds=profile.pause_seconds,
            )
            for profile in VOICE_PROFILES.values()
        ]

    @app.post("/narrate")
    async def narrate_endpoint(body: NarrateRequest, request: Request) -> Response:
        """Narrate a passage, streaming WAV audio as it is synthesised."""
        engine: SynthesisEngine = request.app.state.engine

        # Everything that can be refused is refused here, while a status code is
        # still available. Once the first audio byte goes out, it is not.
        try:
            plan = plan_narration(
                body.text,
                voice_for(body.voice),
                engine,
                max_characters=settings.max_unit_characters(),
                max_total_characters=settings.max_total_characters(),
            )
        except UnknownVoiceError as exc:
            metrics.record_http_error(400)
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        except TextTooLongError as exc:
            metrics.record_http_error(413)
            return JSONResponse(status_code=413, content={"detail": str(exc)})

        if plan.unit_count == 0:
            metrics.record_http_error(400)
            return JSONResponse(
                status_code=400, content={"detail": "Text contains nothing to narrate"}
            )

        logger.info(
            "narration starting",
            extra={
                "units": plan.unit_count,
                "characters": plan.characters,
                "voice": plan.voice.name,
            },
        )
        return StreamingResponse(
            _stream(plan, engine),
            media_type="audio/wav",
            headers={
                # Sent before any audio so a client knows what was promised.
                "X-Narration-Units": str(plan.unit_count),
                "X-Narration-Voice": plan.voice.name,
                "X-Narration-Sample-Rate": str(plan.audio_format.sample_rate),
                "Cache-Control": "no-store",
            },
        )


def _stream(plan: NarrationPlan, engine: SynthesisEngine) -> Iterator[bytes]:
    """Yield a streaming WAV header, then audio.

    A sync generator on purpose: Starlette runs it in a worker thread, which is
    what a compute-bound engine wants anyway.
    """
    started = time.monotonic()
    audio_bytes = 0
    outcome = "ok"

    yield wav_stream_header(plan.audio_format)
    try:
        for chunk in narrate(plan, engine):
            audio_bytes += len(chunk)
            yield chunk
    except EngineError:
        # The response is already 200 and the body is audio; there is nowhere to
        # put an explanation. Record it and end the stream short.
        outcome = "engine_failure"
        metrics.record_engine_failure()
        logger.exception("synthesis failed mid-stream, audio truncated")
    except GeneratorExit:
        outcome = "abandoned"
        raise
    finally:
        metrics.record_request(
            outcome=outcome,
            duration=time.monotonic() - started,
            audio_seconds=plan.audio_format.seconds_for(audio_bytes),
            units=plan.unit_count,
        )
        logger.info(
            "narration finished",
            extra={
                "outcome": outcome,
                "audio_seconds": round(plan.audio_format.seconds_for(audio_bytes), 2),
            },
        )


def main() -> None:
    """Entry point for `python -m gutenberg_narrator.app`."""
    import uvicorn

    uvicorn.run(
        create_application(),
        host=os.environ.get("NARRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("NARRATOR_PORT", "1417")),
    )


if __name__ == "__main__":
    main()
