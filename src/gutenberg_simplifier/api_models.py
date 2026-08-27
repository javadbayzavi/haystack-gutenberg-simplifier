"""Response schema for the deployed pipeline.

Declared as a Pydantic model rather than a bare dict so Hayhooks can generate a
meaningful OpenAPI schema: ``/docs`` then documents the response, not just
``object``.
"""

from pydantic import BaseModel, Field


class SimplifyResponse(BaseModel):
    """A successfully simplified book."""

    book_id: int = Field(description="Project Gutenberg book id that was simplified")
    source_url: str = Field(description="URL the plain text was actually served from")
    size_bytes: int = Field(description="Size of the raw download")
    body_lines: int = Field(description="Lines remaining after boilerplate removal")
    boilerplate_markers_found: bool = Field(
        description="Whether both Gutenberg licence markers were located. False means the "
        "body may still contain front or back matter."
    )
    model: str = Field(description="Model that produced the simplification")
    simplified: str = Field(description="The rewritten story")
