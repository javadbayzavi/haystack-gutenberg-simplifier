"""Assembly of the final result envelope.

Kept apart from both the pipeline and the Hayhooks wrapper so the shape of what
callers receive can be tested without a server and without a model.
"""

from typing import Any

from gutenberg_simplifier.boundaries import BoundaryState, RejectReason
from gutenberg_simplifier.models import BookBody, RawBook
from gutenberg_simplifier.results import SimplificationResult, Status, Usage
from gutenberg_simplifier.simplify import SimplifiedStory
from gutenberg_simplifier.tiers import AgeTier


def build_metadata(
    raw: RawBook,
    body: BookBody,
    boundaries: BoundaryState,
    story: SimplifiedStory,
    *,
    model: str,
) -> dict[str, Any]:
    """Everything an operator needs to explain a given response after the fact."""
    usage: Usage = story.usage
    return {
        "book_id": raw.book_id,
        "source_url": raw.source_url,
        "size_bytes": raw.size_bytes,
        "body_lines": body.line_count,
        "boilerplate_markers_found": body.markers_found,
        "story_start_line": boundaries.start_line,
        "story_end_line": boundaries.end_line,
        "boundary_iterations": boundaries.iterations_used,
        "segments": story.segments,
        "model": model,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "model_calls": usage.calls,
            # None, not 0.0, when the model has no price on file: a zero would
            # read as "this was free" rather than "we do not know".
            "estimated_cost_usd": usage.cost_usd(model),
        },
    }


def build_result(
    raw: RawBook,
    body: BookBody,
    boundaries: BoundaryState,
    story: SimplifiedStory,
    tier: AgeTier,
    *,
    model: str,
) -> SimplificationResult:
    """Turn a completed run into the envelope callers receive.

    A run can fail in two distinguishable ways after fetching succeeds: the
    agent refused the book, or it accepted it and the rewrite still came back
    empty. The second is not a model judgment, so it is reported as ambiguous
    boundaries rather than dressed up as one -- the boundaries were the only
    thing that could have been wrong.
    """
    metadata = build_metadata(raw, body, boundaries, story, model=model)

    if not boundaries.accepted:
        return SimplificationResult.from_rejection(boundaries, tier, metadata=metadata)

    if not story.text.strip():
        return SimplificationResult(
            status=Status.REJECTED,
            tier=tier,
            reject_reason=RejectReason.AMBIGUOUS_BOUNDARIES,
            confidence=boundaries.confidence,
            notes="Boundaries were accepted but produced no rewritable text.",
            metadata=metadata,
        )

    return SimplificationResult(
        status=Status.OK,
        tier=tier,
        content=story.text,
        confidence=boundaries.confidence,
        notes=boundaries.notes,
        metadata=metadata,
    )
