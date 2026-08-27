"""Hayhooks deployment of the simplification pipeline.

Hayhooks discovers this file by convention (``pipelines/<name>/pipeline_wrapper.py``)
and generates the request and response schemas from :meth:`run_api`'s signature.

Error handling splits along a line set in PR 2 and paid off here. Deterministic
failures become HTTP status codes, because they are facts about the request that
HTTP already has vocabulary for -- 404 unknown book, 413 over budget, 502
upstream down. A book the *agent* refused is a different animal: the request
succeeded and reached an honest conclusion, so it answers 200 with
``status: rejected`` and a reason. Collapsing those two into 4xx would tell a
client its request was malformed when it was not.

The mapping for the first kind lives in :mod:`gutenberg_simplifier.api_errors`
-- Haystack wraps component exceptions, so it must unwrap a cause chain, and
that is worth testing without a server.
"""

from collections.abc import AsyncGenerator
from typing import Any

from hayhooks import BasePipelineWrapper, get_last_user_message, log

from gutenberg_simplifier.api_errors import to_http_exception, unwrap
from gutenberg_simplifier.api_models import SimplifyResponse, to_response
from gutenberg_simplifier.assembly import build_result
from gutenberg_simplifier.chat import ChatParseError, parse_chat_request
from gutenberg_simplifier.pipeline import (
    DEFAULT_MODEL,
    build_simplification_pipeline,
    default_generator,
)
from gutenberg_simplifier.streaming import stream_simplification
from gutenberg_simplifier.tiers import AgeTier

_OUTPUTS = {"fetcher", "stripper", "boundary_detector", "simplifier"}


# BasePipelineWrapper resolves to Any because hayhooks ships no py.typed marker;
# strict mode rejects subclassing Any. Scoped to this one line rather than
# relaxing the setting for the whole package.
class PipelineWrapper(BasePipelineWrapper):  # type: ignore[misc]
    def setup(self) -> None:
        """Build once, at server start rather than per request.

        The chat surface keeps its own generators rather than reaching into the
        pipeline's components: streaming needs per-call callbacks, and sharing a
        component instance across concurrent streamed requests would mean
        sharing whatever per-call state it holds.
        """
        self.pipeline = build_simplification_pipeline()
        self.boundary_generator = default_generator()
        self.simplify_generator = default_generator()

    def run_api(
        self,
        book_id: int,
        tier: AgeTier = AgeTier.EARLY_READER,
        max_bytes: int | None = None,
    ) -> SimplifyResponse:
        """Rewrite a Project Gutenberg book for a given reading age.

        Args:
            book_id: Project Gutenberg book id, e.g. 14838 for The Tale of Peter Rabbit.
            tier: Reading-age band: preschool (3-5), early_reader (6-8) or
                middle_grade (9-11).
            max_bytes: Optional override for the size budget. Books larger than
                this are rejected before any model is called.
        """
        fetch_inputs: dict[str, Any] = {"book_id": book_id}
        if max_bytes is not None:
            fetch_inputs["max_bytes"] = max_bytes

        log.info("simplify requested", book_id=book_id, tier=tier.value, max_bytes=max_bytes)

        try:
            result = self.pipeline.run(
                {"fetcher": fetch_inputs, "simplifier": {"tier": tier}},
                include_outputs_from=_OUTPUTS,
            )
        except Exception as exc:
            http_error = to_http_exception(exc)
            # The client gets a sanitised detail; the operator needs the real
            # cause, or a 500 is undebuggable from the logs alone.
            log.warning(
                "simplify failed",
                book_id=book_id,
                status_code=http_error.status_code,
                detail=http_error.detail,
                cause=repr(unwrap(exc)),
            )
            raise http_error from exc

        envelope = build_result(
            result["fetcher"]["book"],
            result["stripper"]["body"],
            result["boundary_detector"]["boundaries"],
            result["simplifier"]["story"],
            tier,
            model=DEFAULT_MODEL,
        )
        log.info(
            "simplify finished",
            book_id=book_id,
            status=envelope.status.value,
            reject_reason=envelope.reject_reason.value if envelope.reject_reason else None,
            segments=envelope.metadata.get("segments"),
        )
        return to_response(envelope)

    async def run_chat_completion_async(
        self, model: str, messages: list[dict[str, Any]], body: dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """Stream a simplified book over the OpenAI-compatible chat endpoint.

        Args:
            model: The deployed pipeline name, supplied by the caller's client.
            messages: OpenAI-format chat history. Only the last user message is
                read -- this is a request surface, not a conversation.
            body: Additional OpenAI parameters. Unused.
        """
        question = get_last_user_message(messages)

        try:
            request = parse_chat_request(question)
        except ChatParseError as exc:
            # A stream has already committed to 200, and the caller is a person.
            # Hand them the sentence, not a status code.
            log.info("chat request unparsed", detail=str(exc))
            return _single_message(str(exc))

        log.info("chat simplify requested", book_id=request.book_id, tier=request.tier.value)
        return stream_simplification(
            request.book_id,
            request.tier,
            boundary_generator=self.boundary_generator,
            simplify_generator=self.simplify_generator,
        )


async def _single_message(text: str) -> AsyncGenerator[str, None]:
    """Wrap one string as a stream, so both branches return the same type."""
    yield text
