"""Streaming simplification for the OpenAI-compatible chat surface.

What streams, and what does not, is a deliberate split:

*The prose streams token by token.* That is the part a reader waits on, and it
is the reason this endpoint exists.

*The phases before it emit progress lines, not model output.* Fetching, boundary
detection and segmentation take real time, and a chat client showing nothing for
several seconds looks broken. But the boundary agent's own chatter is reasoning
about a book, not the book -- streaming it would leak half-formed judgments and,
worse, fragments of the source text it was told never to reproduce. So those
phases report *that* they are happening, never what the model is saying.

Errors read differently here than on ``/simplify/run``. A stream commits to HTTP
200 the moment its first byte leaves, so a failure afterwards cannot become a
status code. It is written as a sentence instead. That is not a workaround: this
surface is talking to a person, while the REST surface is talking to a program,
and each gets the failure form it can actually use.

That applies to a failing *model* too, not only a failing fetch. Letting the
exception escape mid-stream would deliver a partial story followed by a dropped
connection, which a reader cannot distinguish from a story that simply ended.
Every phase therefore ends in a visible marker, and the real cause goes to the
log where an operator can find it.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable

from haystack.core.component import Component
from haystack.dataclasses import ChatMessage, StreamingChunk

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.boundaries import BoundaryState, detect_boundaries
from gutenberg_simplifier.errors import GutenbergSimplifierError
from gutenberg_simplifier.fallback import apply_boundary_fallback
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES, fetch_book
from gutenberg_simplifier.models import RawBook
from gutenberg_simplifier.simplify import (
    DEFAULT_SEGMENT_LINES,
    build_segment_message,
    build_system_message,
    segment_story,
    story_lines,
)
from gutenberg_simplifier.tiers import AgeTier, guidance_for

FetchFn = Callable[[int, int], RawBook]

logger = logging.getLogger(__name__)


def _progress(message: str) -> str:
    """A status line, visually distinct from the story itself."""
    return f"_{message}_\n"


def _default_fetch(book_id: int, max_bytes: int) -> RawBook:
    return fetch_book(book_id, max_bytes=max_bytes)


async def stream_simplification(
    book_id: int,
    tier: AgeTier,
    *,
    boundary_generator: Component,
    simplify_generator: Component,
    fetch: FetchFn | None = None,
    max_bytes: int = DEFAULT_MAX_BOOK_BYTES,
    max_lines: int = DEFAULT_SEGMENT_LINES,
) -> AsyncGenerator[str, None]:
    """Yield progress lines, then the rewritten story token by token."""
    fetch_fn = fetch or _default_fetch

    yield _progress(f"Fetching book {book_id}")
    try:
        # Both are blocking: network, then a full-body regex scan.
        raw = await asyncio.to_thread(fetch_fn, book_id, max_bytes)
        body = await asyncio.to_thread(strip_gutenberg_boilerplate, raw)
    except GutenbergSimplifierError as exc:
        yield _progress(str(exc))
        return

    yield _progress(f"Looking for the story in {body.line_count} lines")
    try:
        boundaries = await asyncio.to_thread(detect_boundaries, body, boundary_generator)
    except Exception:
        # Nothing of value has streamed yet, so this degrades cleanly.
        logger.exception("boundary detection failed", extra={"book_id": book_id})
        yield _progress("I could not read this book just now. Please try again.")
        return

    boundaries = apply_boundary_fallback(body, boundaries)
    if not boundaries.accepted:
        yield _progress(_explain_rejection(boundaries))
        return
    if boundaries.fallback_applied:
        yield _progress("I could not pin down where the story starts, so I will rewrite all of it")

    segments = segment_story(story_lines(body, boundaries), max_lines=max_lines)
    if not segments:
        yield _progress("The story boundaries did not contain any text to rewrite.")
        return

    guidance = guidance_for(tier)
    yield _progress(
        f"Rewriting for ages {guidance.age_range} in {len(segments)} "
        f"part{'s' if len(segments) != 1 else ''}"
    )
    yield "\n"

    system = build_system_message(tier)

    previous = ""
    for index, segment in enumerate(segments):
        if index:
            yield "\n\n"

        user = build_segment_message(segment, index=index, total=len(segments), previous=previous)

        buffer: list[str] = []
        try:
            async for token in _stream_one_call(simplify_generator, [system, user]):
                buffer.append(token)
                yield token
        except asyncio.CancelledError:
            raise  # the consumer walked away; not our failure to report
        except Exception:
            # Prose is already on the wire. Re-raising here would drop the
            # connection and leave a partial story that reads like a finished
            # one, so say plainly that it stopped early.
            logger.exception(
                "simplification failed mid-stream",
                extra={"book_id": book_id, "part": index + 1},
            )
            yield "\n\n"
            yield _progress(
                f"The rewrite stopped partway through part {index + 1} "
                f"of {len(segments)}. The story above is incomplete."
            )
            return
        previous = "".join(buffer).strip()


def _explain_rejection(boundaries: BoundaryState) -> str:
    """Turn a rejection into something a person can act on."""
    reason = boundaries.reject_reason.value if boundaries.reject_reason else "unknown"
    detail = f" {boundaries.notes}" if boundaries.notes else ""
    return f"I could not simplify this book ({reason}).{detail}"


async def _stream_one_call(
    chat_generator: Component,
    messages: list[ChatMessage],
) -> AsyncIterator[str]:
    """Stream one model call's text, chunk by chunk.

    The generator writes into a queue from its callback while this iterator
    drains it. If the consumer walks away -- a client disconnecting mid-story is
    the normal case, not an exceptional one -- the ``finally`` cancels the
    in-flight call rather than leaving it billing tokens into a closed socket.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def callback(chunk: StreamingChunk) -> None:
        if chunk.content:
            await queue.put(chunk.content)

    async def run() -> None:
        try:
            await chat_generator.run_async(  # type: ignore[attr-defined]
                messages=messages, streaming_callback=callback
            )
        finally:
            await queue.put(None)

    task: asyncio.Task[None] = asyncio.create_task(run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        await task  # surface any error the call raised
    finally:
        if not task.done():
            task.cancel()
