"""Hayhooks deployment of the simplification pipeline.

Hayhooks discovers this file by convention (``pipelines/<name>/pipeline_wrapper.py``)
and generates the request and response schemas from :meth:`run_api`'s signature.

On error handling: deterministic failures become HTTP status codes, because they
are facts about the request that HTTP already has vocabulary for -- an unknown
book id is a 404, an oversized book is a 413. The structured
``status``/``reason`` envelope from the design is deliberately *not* used here.
It earns its place in PR 3, where the agent produces judgments ("boundaries were
ambiguous", "text looked corrupted") that no HTTP status expresses honestly.

The mapping itself lives in :mod:`gutenberg_simplifier.api_errors` -- Haystack
wraps component exceptions, so it needs to unwrap a cause chain, and that is
worth testing without a server.
"""

from typing import Any

from fastapi import HTTPException
from hayhooks import BasePipelineWrapper, log

from gutenberg_simplifier.api_errors import to_http_exception, unwrap
from gutenberg_simplifier.api_models import SimplifyResponse
from gutenberg_simplifier.pipeline import DEFAULT_MODEL, build_simplification_pipeline


# BasePipelineWrapper resolves to Any because hayhooks ships no py.typed marker;
# strict mode rejects subclassing Any. Scoped to this one line rather than
# relaxing the setting for the whole package.
class PipelineWrapper(BasePipelineWrapper):  # type: ignore[misc]
    def setup(self) -> None:
        """Build the pipeline once, at server start rather than per request."""
        self.pipeline = build_simplification_pipeline()

    def run_api(self, book_id: int, max_bytes: int | None = None) -> SimplifyResponse:
        """Simplify a Project Gutenberg book for a young reader.

        Args:
            book_id: Project Gutenberg book id, e.g. 14838 for The Tale of Peter Rabbit.
            max_bytes: Optional override for the size budget. Books larger than
                this are rejected before any model is called.
        """
        inputs: dict[str, Any] = {"book_id": book_id}
        if max_bytes is not None:
            inputs["max_bytes"] = max_bytes

        log.info("simplify requested", book_id=book_id, max_bytes=max_bytes)

        try:
            result = self.pipeline.run(
                {"fetcher": inputs},
                include_outputs_from={"fetcher", "stripper"},
            )
        except Exception as exc:
            # Haystack wraps component errors, so the deliberate rejections are
            # only reachable through the cause chain. See api_errors.unwrap.
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

        raw = result["fetcher"]["book"]
        body = result["stripper"]["body"]
        replies = result["generator"]["replies"]
        if not replies or not replies[0].text:
            raise HTTPException(status_code=502, detail="Model returned no content")

        return SimplifyResponse(
            book_id=body.book_id,
            source_url=raw.source_url,
            size_bytes=raw.size_bytes,
            body_lines=body.line_count,
            boilerplate_markers_found=body.markers_found,
            model=DEFAULT_MODEL,
            simplified=replies[0].text,
        )
