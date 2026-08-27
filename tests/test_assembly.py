"""The result envelope: what callers actually receive."""

from gutenberg_simplifier.api_models import to_response
from gutenberg_simplifier.assembly import build_result
from gutenberg_simplifier.boundaries import BoundaryState, Confidence, RejectReason
from gutenberg_simplifier.models import BookBody, RawBook
from gutenberg_simplifier.results import SCHEMA_VERSION, Status, Usage
from gutenberg_simplifier.simplify import SimplifiedStory
from gutenberg_simplifier.tiers import AgeTier

RAW = RawBook(book_id=14838, text="x", source_url="https://example.test/1", size_bytes=25_952)
BODY = BookBody(book_id=14838, lines=("a", "b"), start_line=30, markers_found=True)
ACCEPTED = BoundaryState(
    start_line=34,
    end_line=200,
    reject_reason=None,
    confidence=Confidence.HIGH,
    notes="Story runs from the title to THE END.",
    iterations_used=4,
)
STORY = SimplifiedStory(text="A rabbit ate a carrot.", usage=Usage(1000, 500, 2), segments=2)


def _build(boundaries: BoundaryState, story: SimplifiedStory):  # type: ignore[no-untyped-def]
    return build_result(RAW, BODY, boundaries, story, AgeTier.PRESCHOOL, model="claude-opus-5")


def test_successful_run_carries_content_and_accounting() -> None:
    result = _build(ACCEPTED, STORY)

    assert result.status is Status.OK
    assert result.content == "A rabbit ate a carrot."
    assert result.reject_reason is None
    assert result.schema_version == SCHEMA_VERSION
    assert result.metadata["usage"]["model_calls"] == 2
    assert result.metadata["usage"]["estimated_cost_usd"] is not None
    assert result.metadata["segments"] == 2
    assert result.metadata["boundary_iterations"] == 4


def test_rejection_carries_the_agents_own_reason_and_confidence() -> None:
    """Nothing here upgrades a low-confidence refusal into something firmer."""
    rejected = BoundaryState.rejected(
        RejectReason.CORRUPTED_TEXT,
        notes="OCR noise throughout.",
        iterations_used=3,
        confidence=Confidence.LOW,
    )

    result = _build(rejected, SimplifiedStory(text="", usage=Usage(), segments=0))

    assert result.status is Status.REJECTED
    assert result.reject_reason is RejectReason.CORRUPTED_TEXT
    assert result.confidence is Confidence.LOW
    assert result.notes == "OCR noise throughout."
    assert result.content is None


def test_accepted_boundaries_with_no_output_is_reported_honestly() -> None:
    """Not a model judgment, so it is not dressed up as one."""
    result = _build(ACCEPTED, SimplifiedStory(text="   ", usage=Usage(), segments=1))

    assert result.status is Status.REJECTED
    assert result.reject_reason is RejectReason.AMBIGUOUS_BOUNDARIES
    assert "no rewritable text" in result.notes


def test_metadata_is_present_even_on_a_rejection() -> None:
    """An operator must be able to explain a refusal after the fact."""
    rejected = BoundaryState.rejected(
        RejectReason.BUDGET_EXHAUSTED, notes="ran out", iterations_used=40
    )

    result = _build(rejected, SimplifiedStory(text="", usage=Usage(), segments=0))

    assert result.metadata["book_id"] == 14838
    assert result.metadata["source_url"] == "https://example.test/1"
    assert result.metadata["boundary_iterations"] == 40


def test_wire_mapping_is_json_serialisable_and_flattens_enums() -> None:
    payload = to_response(_build(ACCEPTED, STORY)).model_dump()

    assert payload["status"] == "ok"
    assert payload["tier"] == "preschool"
    assert payload["confidence"] == "high"
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["metadata"]["usage"]["input_tokens"] == 1000


def test_wire_mapping_of_a_rejection() -> None:
    rejected = BoundaryState.rejected(
        RejectReason.NO_STORY_FOUND, notes="a dictionary", iterations_used=2
    )
    payload = to_response(_build(rejected, SimplifiedStory("", Usage(), 0))).model_dump()

    assert payload["status"] == "rejected"
    assert payload["reject_reason"] == "no_story_found"
    assert payload["content"] is None
