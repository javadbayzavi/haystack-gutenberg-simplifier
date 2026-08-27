"""Runs the golden set and reports a pass/fail table.

Evaluated with the **fallback disabled**. The fallback deliberately turns a
non-converging search into an accepted low-confidence result, which is right for
serving a request and wrong for measuring the agent: it would quietly convert
every ambiguous case into a pass. What is measured here is the agent's own
judgment.
"""

import os
import time
from dataclasses import dataclass

from haystack.core.component import Component

from evals.cases import CASES, EvalCase
from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.boundaries import BoundaryState, RejectReason, detect_boundaries
from gutenberg_simplifier.models import RawBook


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case: EvalCase
    outcome: RejectReason | None
    passed: bool
    confidence: str
    iterations: int
    seconds: float
    error: str | None = None

    @property
    def actual(self) -> str:
        if self.error:
            return f"error: {self.error}"
        return self.outcome.value if self.outcome else "accepted"

    @property
    def expected(self) -> str:
        return ", ".join(
            sorted(reason.value if reason else "accepted" for reason in self.case.allowed)
        )


def run_case(case: EvalCase, chat_generator: Component) -> CaseOutcome:
    """Run one fixture through fetch-free ingestion and the boundary agent."""
    text = case.text()
    raw = RawBook(
        book_id=0,
        text=text,
        source_url=f"file://{case.fixture}",
        size_bytes=len(text.encode()),
    )
    body = strip_gutenberg_boilerplate(raw)

    started = time.monotonic()
    try:
        state: BoundaryState = detect_boundaries(body, chat_generator)
    # Deliberately broad: one case blowing up must not abort the run. A crashed
    # case is reported as a failed case, with the exception text in the table.
    except Exception as exc:  # noqa: BLE001
        return CaseOutcome(
            case=case,
            outcome=None,
            passed=False,
            confidence="-",
            iterations=0,
            seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    return CaseOutcome(
        case=case,
        outcome=state.reject_reason,
        passed=state.reject_reason in case.allowed,
        confidence=state.confidence.value,
        iterations=state.iterations_used,
        seconds=time.monotonic() - started,
    )


def run_all(chat_generator: Component, cases: tuple[EvalCase, ...] = CASES) -> list[CaseOutcome]:
    return [run_case(case, chat_generator) for case in cases]


def format_table(outcomes: list[CaseOutcome]) -> str:
    """Render the results as a fixed-width table."""
    headers = ("case", "expected", "actual", "conf", "iters", "time", "result")
    rows = [
        (
            outcome.case.name,
            outcome.expected,
            outcome.actual,
            outcome.confidence,
            str(outcome.iterations),
            f"{outcome.seconds:.1f}s",
            "PASS" if outcome.passed else "FAIL",
        )
        for outcome in outcomes
    ]

    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    line = "  ".join("-" * width for width in widths)
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)), line]
    out += ["  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)) for row in rows]

    passed = sum(1 for outcome in outcomes if outcome.passed)
    out += [line, f"{passed}/{len(outcomes)} passed"]

    failures = [o for o in outcomes if not o.passed]
    if failures:
        out.append("")
        out.append("Failures:")
        out += [f"  {o.case.name}: expected {o.expected}, got {o.actual}" for o in failures]
    return "\n".join(out)


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
