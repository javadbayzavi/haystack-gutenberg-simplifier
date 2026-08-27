"""The golden set.

Each case asserts a **decision**, never prose. Asserting the rewritten text
would test the model's word choice, which drifts between runs and between model
versions and tells you nothing about whether the pipeline works. What must hold
is that a manual is refused, a garbled scan is refused, and a clean story is
not.

Expectations are sets, not single values, because some cases genuinely have more
than one defensible answer. A truncated story may be reported as ambiguous or
accepted with the text that exists -- both are honest. What matters is that it
is never called corrupted or refused as a non-story. Encoding "any of these" is
more useful than pretending there is one right answer and loosening the test
later when it fails for a good reason.
"""

from dataclasses import dataclass
from pathlib import Path

from gutenberg_simplifier.boundaries import RejectReason
from gutenberg_simplifier.tiers import AgeTier

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Sentinel for "accepted, no rejection".
ACCEPTED: None = None


@dataclass(frozen=True, slots=True)
class EvalCase:
    name: str
    fixture: str
    description: str
    #: Outcomes that count as correct. ``None`` means accepted.
    allowed: frozenset[RejectReason | None]
    tier: AgeTier = AgeTier.EARLY_READER

    def text(self) -> str:
        return (FIXTURE_DIR / self.fixture).read_text(encoding="utf-8")


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="clean_story",
        fixture="clean_story.txt",
        description="An ordinary children's story with normal front matter",
        allowed=frozenset({ACCEPTED}),
    ),
    EvalCase(
        name="heavy_front_matter",
        fixture="heavy_front_matter.txt",
        description="Dedication, publisher's note, contents and plate list before the story",
        allowed=frozenset({ACCEPTED}),
    ),
    EvalCase(
        name="ocr_garbled",
        fixture="ocr_garbled.txt",
        description="A damaged scan: digit substitutions and a missing page",
        allowed=frozenset({RejectReason.CORRUPTED_TEXT}),
    ),
    EvalCase(
        name="not_a_story",
        fixture="not_a_story.txt",
        description="A technical manual with tables and numbered sections",
        allowed=frozenset({RejectReason.NO_STORY_FOUND}),
    ),
    EvalCase(
        name="truncated_story",
        fixture="truncated_story.txt",
        description="A story that stops mid-sentence with no ending",
        # Both readings are honest; calling it corrupted or a non-story is not.
        allowed=frozenset({ACCEPTED, RejectReason.AMBIGUOUS_BOUNDARIES}),
    ),
)
