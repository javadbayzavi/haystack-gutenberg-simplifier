"""The versioned result envelope.

Every response carries ``schema_version``. Consumers of an LLM pipeline break on
shape changes far more often than on content changes, and this is the cheapest
possible way to let a client notice.

The split between ``status`` here and the HTTP status code is deliberate: HTTP
codes describe facts about the request (unknown book, over budget, upstream
down), while ``status: rejected`` describes a *judgment* the
model made (the text looked corrupted, the boundaries were ambiguous). A refused
book is a successful request that reached an honest conclusion, so it answers
200 with a reason -- not 4xx.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from gutenberg_simplifier.boundaries import BoundaryState, Confidence, RejectReason
from gutenberg_simplifier.tiers import AgeTier

#: Bump on any change to the envelope's shape.
SCHEMA_VERSION = "1.0"


class Status(StrEnum):
    OK = "ok"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Usage:
    """Token and call accounting, summed across every model call in a request."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            calls=self.calls + other.calls,
        )

    def cost_usd(self, model: str) -> float | None:
        """Estimated spend, or None for a model with no price on file.

        Returning None rather than 0.0 matters: a silent zero reads as "this was
        free", which is the opposite of "we do not know".
        """
        price = _PRICING_USD_PER_MTOK.get(model)
        if price is None:
            return None
        input_price, output_price = price
        return round(
            (self.input_tokens * input_price + self.output_tokens * output_price) / 1_000_000,
            6,
        )


#: USD per million tokens, (input, output). Kept here so a stale price is one
#: obvious edit rather than a number buried in a formula.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass(frozen=True, slots=True)
class SimplificationResult:
    """What the API returns, whether or not the book was simplified."""

    status: Status
    tier: AgeTier
    content: str | None = None
    reject_reason: RejectReason | None = None
    confidence: Confidence = Confidence.MEDIUM
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_rejection(
        cls,
        boundaries: BoundaryState,
        tier: AgeTier,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> "SimplificationResult":
        """Carry the agent's judgment through unchanged.

        The reason and confidence are the model's own; nothing here upgrades a
        low-confidence refusal into something more certain than it was.
        """
        return cls(
            status=Status.REJECTED,
            tier=tier,
            content=None,
            reject_reason=boundaries.reject_reason,
            confidence=boundaries.confidence,
            notes=boundaries.notes,
            metadata=metadata or {},
        )
