"""Degrading a failed boundary search instead of failing the request.

The distinction that matters here is *why* the agent said no.

A refusal about the **content** -- this is a dictionary, the text is garbled,
this is not suitable for a child -- is a judgment, and it stands. Overriding it
would mean shipping exactly the output the refusal existed to prevent.

A failure of **process** -- the budget ran out, the boundaries were too
ambiguous to call -- says nothing about whether the book is fit to simplify. It
says the search did not converge. There the whole stripped body is a defensible
second-best: it is the text the licence markers already identified, and it may
carry some front matter the agent would have trimmed. So the request degrades
to that, marked low confidence and flagged, rather than returning nothing.

Marking it is the part that keeps this honest. A silent fallback would make a
guessed range indistinguishable from a located one.
"""

from gutenberg_simplifier.boundaries import BoundaryState, Confidence, RejectReason
from gutenberg_simplifier.models import BookBody

#: Failures of process, not judgments about content.
FALLBACK_ELIGIBLE: frozenset[RejectReason] = frozenset(
    {
        RejectReason.BUDGET_EXHAUSTED,
        RejectReason.AMBIGUOUS_BOUNDARIES,
    }
)


def apply_boundary_fallback(body: BookBody, boundaries: BoundaryState) -> BoundaryState:
    """Return usable boundaries when the search failed for a process reason.

    Accepted results and content refusals are returned unchanged.
    """
    if boundaries.accepted:
        return boundaries
    if boundaries.reject_reason not in FALLBACK_ELIGIBLE:
        return boundaries
    if body.line_count == 0:
        # Nothing to fall back *to*; a guess over an empty body is not a result.
        return boundaries

    original = boundaries.reject_reason.value if boundaries.reject_reason else "unknown"
    return BoundaryState(
        start_line=body.start_line,
        end_line=body.start_line + body.line_count - 1,
        reject_reason=None,
        confidence=Confidence.LOW,
        notes=(
            f"Boundary search did not converge ({original}); using the whole "
            f"de-boilerplated body. Front or back matter may remain."
        ),
        iterations_used=boundaries.iterations_used,
        fallback_applied=True,
    )
