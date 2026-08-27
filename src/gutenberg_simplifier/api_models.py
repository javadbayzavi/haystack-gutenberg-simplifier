"""Wire schema for the deployed pipeline.

Declared as Pydantic models rather than bare dicts so Hayhooks generates a
meaningful OpenAPI schema: ``/docs`` documents the response, not ``object``.

Deliberately separate from :class:`SimplificationResult`. That one is the domain
object; this one is the contract. Keeping them apart means a refactor of the
former cannot silently change what clients receive -- the mapping below has to
be edited on purpose.
"""

from typing import Any

from pydantic import BaseModel, Field

from gutenberg_simplifier.results import SimplificationResult


class UsageModel(BaseModel):
    input_tokens: int = Field(description="Prompt tokens across every model call")
    output_tokens: int = Field(description="Generated tokens across every model call")
    model_calls: int = Field(description="Number of model calls made for this request")
    estimated_cost_usd: float | None = Field(
        default=None,
        description="Estimated spend. Null when the model has no price on file, "
        "which is different from zero.",
    )


class MetadataModel(BaseModel):
    book_id: int
    source_url: str
    size_bytes: int
    body_lines: int = Field(description="Lines remaining after boilerplate removal")
    boilerplate_markers_found: bool
    story_start_line: int | None = Field(
        default=None, description="First line of the story, in raw-text line numbers"
    )
    story_end_line: int | None = Field(
        default=None, description="Last line of the story, in raw-text line numbers"
    )
    boundary_iterations: int = Field(description="Chunks the boundary agent read")
    boundary_fallback_applied: bool = Field(
        default=False,
        description="True when the boundary search did not converge and the whole "
        "de-boilerplated body was used instead. A guessed range, not a located one.",
    )
    segments: int = Field(description="Segments the story was rewritten in")
    model: str
    usage: UsageModel


class SimplifyResponse(BaseModel):
    """The result envelope. Carries a schema_version so clients can notice change."""

    schema_version: str
    status: str = Field(description="'ok' or 'rejected'")
    tier: str = Field(description="Reading-age tier the rewrite targeted")
    content: str | None = Field(default=None, description="The rewritten story; null if rejected")
    reject_reason: str | None = Field(
        default=None,
        description="Why the book was refused: corrupted_text, inappropriate_content, "
        "no_story_found, ambiguous_boundaries, budget_exhausted",
    )
    confidence: str = Field(description="high, medium or low")
    notes: str = Field(default="", description="What the agent observed. Never book text.")
    metadata: MetadataModel


def to_response(result: SimplificationResult) -> SimplifyResponse:
    """Map the domain result onto the wire contract."""
    metadata: dict[str, Any] = result.metadata
    return SimplifyResponse(
        schema_version=result.schema_version,
        status=result.status.value,
        tier=result.tier.value,
        content=result.content,
        reject_reason=result.reject_reason.value if result.reject_reason else None,
        confidence=result.confidence.value,
        notes=result.notes,
        metadata=MetadataModel(**metadata),
    )
