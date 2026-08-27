"""Agentic detection of where a story starts and ends.

The division of labour is the whole design. :class:`ChunkReader` decides *when*
to stop -- it advances the cursor, counts iterations, and returns nothing once
the budget is spent. The model decides only *what it is looking at*. So the loop
terminates because of code with no model in it, and a model that never
cooperates costs a bounded number of turns and yields a rejection, not a hang.

Two tools rather than the single ``read_next_chunk`` originally sketched: the
decision comes back through a ``record_decision`` tool call so its shape is
enforced by a JSON schema, instead of being parsed out of free text. That also
gives the agent a precise exit condition.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from haystack.components.agents import Agent
from haystack.core.component import Component
from haystack.dataclasses import ChatMessage

# Imported from their defining modules: haystack.tools re-exports these without
# an explicit __all__, which mypy's strict mode will not follow.
from haystack.tools.from_function import create_tool_from_function
from haystack.tools.tool import Tool

from gutenberg_simplifier.chunking import ChunkReader
from gutenberg_simplifier.models import BookBody
from gutenberg_simplifier.prompts import BOUNDARY_SYSTEM

#: Agent steps allowed beyond the read budget: room to think and to record.
STEP_SLACK = 5


class RejectReason(StrEnum):
    """Why a book was refused. Reported, never raised."""

    CORRUPTED_TEXT = "corrupted_text"
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    NO_STORY_FOUND = "no_story_found"
    AMBIGUOUS_BOUNDARIES = "ambiguous_boundaries"
    #: Not a model judgment: the reader ran out of budget first.
    BUDGET_EXHAUSTED = "budget_exhausted"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class BoundaryState:
    """The outcome of a detection run.

    ``start_line`` and ``end_line`` are absolute raw-text line numbers, and are
    populated only when ``reject_reason`` is ``None``.
    """

    start_line: int | None
    end_line: int | None
    reject_reason: RejectReason | None
    confidence: Confidence
    notes: str
    iterations_used: int

    @property
    def accepted(self) -> bool:
        return self.reject_reason is None

    @classmethod
    def rejected(
        cls,
        reason: RejectReason,
        *,
        notes: str,
        iterations_used: int,
        confidence: Confidence = Confidence.LOW,
    ) -> "BoundaryState":
        return cls(
            start_line=None,
            end_line=None,
            reject_reason=reason,
            confidence=confidence,
            notes=notes,
            iterations_used=iterations_used,
        )


class _BoundarySession:
    """Holds the reader and the decision for one detection run.

    One instance per run: the tools close over it, so two concurrent runs never
    share a cursor.
    """

    def __init__(self, reader: ChunkReader) -> None:
        self.reader = reader
        self.decision: BoundaryState | None = None

    def read_next_chunk(self) -> str:
        """Read the next part of the book.

        Returns:
            The next chunk with its line numbers, or a notice that the book is
            finished and a decision must now be recorded.
        """
        chunk = self.reader.read_next()
        if chunk is None:
            return (
                "No more chunks are available. You have seen everything you are "
                "going to see. Call record_decision now with what you know."
            )
        return (
            f"Lines {chunk.start_line}-{chunk.end_line - 1} "
            f"(chunk {chunk.index + 1}, budget {self.reader.iterations_used}/"
            f"{self.reader.max_iterations}):\n{chunk.text}"
        )

    def record_decision(
        self,
        found: bool,
        start_line: int | None = None,
        end_line: int | None = None,
        reject_reason: str | None = None,
        confidence: str = "medium",
        notes: str = "",
    ) -> str:
        """Record where the story begins and ends, or why it was refused.

        Args:
            found: True if both boundaries were located, False to reject.
            start_line: Absolute line number of the story's first line.
            end_line: Absolute line number of the story's last line.
            reject_reason: One of corrupted_text, inappropriate_content,
                no_story_found, ambiguous_boundaries. Required when found is False.
            confidence: One of high, medium, low.
            notes: Brief description of what was found. Never book text.
        """
        self.decision = _build_decision(
            found=found,
            start_line=start_line,
            end_line=end_line,
            reject_reason=reject_reason,
            confidence=confidence,
            notes=notes,
            iterations_used=self.reader.iterations_used,
        )
        return "Decision recorded."


def _build_decision(
    *,
    found: bool,
    start_line: int | None,
    end_line: int | None,
    reject_reason: str | None,
    confidence: str,
    notes: str,
    iterations_used: int,
) -> BoundaryState:
    """Normalise whatever the model sent into a valid BoundaryState.

    The model can be wrong in ways the JSON schema cannot catch -- claiming a
    find with no line numbers, or naming a reason that is not in the taxonomy.
    Those become rejections here rather than propagating as bad data.
    """
    resolved_confidence = _coerce_confidence(confidence)

    if not found:
        reason = _coerce_reject_reason(reject_reason)
        return BoundaryState.rejected(
            reason,
            notes=notes or "No reason given.",
            iterations_used=iterations_used,
            confidence=resolved_confidence,
        )

    if start_line is None or end_line is None:
        return BoundaryState.rejected(
            RejectReason.AMBIGUOUS_BOUNDARIES,
            notes="Reported a find without both line numbers.",
            iterations_used=iterations_used,
        )
    if end_line < start_line:
        return BoundaryState.rejected(
            RejectReason.AMBIGUOUS_BOUNDARIES,
            notes=f"Reported an inverted range ({start_line} to {end_line}).",
            iterations_used=iterations_used,
        )

    return BoundaryState(
        start_line=start_line,
        end_line=end_line,
        reject_reason=None,
        confidence=resolved_confidence,
        notes=notes,
        iterations_used=iterations_used,
    )


def _coerce_confidence(value: str) -> Confidence:
    try:
        return Confidence(str(value).strip().lower())
    except ValueError:
        return Confidence.LOW


def _coerce_reject_reason(value: str | None) -> RejectReason:
    """Map a model-supplied reason onto the taxonomy.

    An unrecognised reason is not trusted as a category; it becomes
    AMBIGUOUS_BOUNDARIES, which is the honest reading of "refused for a reason
    we do not have a bucket for".
    """
    if value is None:
        return RejectReason.AMBIGUOUS_BOUNDARIES
    try:
        return RejectReason(str(value).strip().lower())
    except ValueError:
        return RejectReason.AMBIGUOUS_BOUNDARIES


def build_boundary_tools(session: _BoundarySession) -> list[Tool]:
    """Expose the session's two methods as agent tools."""
    return [
        create_tool_from_function(
            session.read_next_chunk,
            name="read_next_chunk",
            description="Read the next part of the book, in order.",
        ),
        create_tool_from_function(
            session.record_decision,
            name="record_decision",
            description="Record the story's boundaries, or why the book was refused.",
        ),
    ]


def detect_boundaries(
    body: BookBody,
    chat_generator: Component,
    *,
    reader_factory: Callable[[BookBody], ChunkReader] | None = None,
) -> BoundaryState:
    """Locate the story inside ``body``.

    Always returns a :class:`BoundaryState`; never raises for a book it cannot
    handle. A model that never records a decision is reported as
    BUDGET_EXHAUSTED once the agent's step ceiling is reached.
    """
    reader = reader_factory(body) if reader_factory else ChunkReader(body)
    session = _BoundarySession(reader)

    agent = Agent(
        chat_generator=chat_generator,  # type: ignore[arg-type]
        tools=build_boundary_tools(session),
        system_prompt=BOUNDARY_SYSTEM,
        exit_conditions=["record_decision"],
        max_agent_steps=reader.max_iterations + STEP_SLACK,
        raise_on_tool_invocation_failure=False,
    )

    agent.run(messages=[ChatMessage.from_user("Find the story in this book.")])

    if session.decision is not None:
        return session.decision

    # The agent stopped without recording anything: it hit the step ceiling, or
    # ended its turn without calling the tool. Either way the budget is what
    # ended the run, and that is a rejection rather than a crash.
    return BoundaryState.rejected(
        RejectReason.BUDGET_EXHAUSTED,
        notes=(
            f"No decision recorded within {reader.max_iterations} reads "
            f"({reader.iterations_used} used)."
        ),
        iterations_used=reader.iterations_used,
    )


def as_dict(state: BoundaryState) -> dict[str, Any]:
    """Flatten a state for logging or an API response."""
    return {
        "accepted": state.accepted,
        "start_line": state.start_line,
        "end_line": state.end_line,
        "reject_reason": state.reject_reason.value if state.reject_reason else None,
        "confidence": state.confidence.value,
        "notes": state.notes,
        "iterations_used": state.iterations_used,
    }
