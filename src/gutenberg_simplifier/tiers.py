"""Reading-age tiers.

Each tier carries its own guidance rather than a number the prompt interpolates
into a sentence. "Rewrite this for a 7 year old" leans on whatever the model
believes about 7 year olds; naming the sentence length, vocabulary and structure
directly is both more reliable and more reviewable -- a children's librarian can
read this file and tell you it is wrong, which they could not do with an integer.
"""

from dataclasses import dataclass
from enum import StrEnum


class AgeTier(StrEnum):
    """Reading age bands. The value is the API's wire format."""

    PRESCHOOL = "preschool"
    EARLY_READER = "early_reader"
    MIDDLE_GRADE = "middle_grade"


@dataclass(frozen=True, slots=True)
class TierGuidance:
    tier: AgeTier
    age_range: str
    guidance: str


_GUIDANCE: dict[AgeTier, TierGuidance] = {
    AgeTier.PRESCHOOL: TierGuidance(
        tier=AgeTier.PRESCHOOL,
        age_range="3 to 5",
        guidance=(
            "Sentences of at most 8 words, one idea each.\n"
            "Only words a preschooler hears in conversation.\n"
            "Name characters by what they are ('the rabbit'), not by pronoun chains.\n"
            "Say events in the order they happen. No flashbacks, no asides.\n"
            "Keep frightening moments, but state them plainly and briefly."
        ),
    ),
    AgeTier.EARLY_READER: TierGuidance(
        tier=AgeTier.EARLY_READER,
        age_range="6 to 8",
        guidance=(
            "Sentences of at most 14 words. Two clauses at most.\n"
            "Everyday vocabulary; explain an unusual word in the sentence that uses it.\n"
            "Keep dialogue, but keep it short.\n"
            "Preserve cause and effect explicitly ('so', 'because')."
        ),
    ),
    AgeTier.MIDDLE_GRADE: TierGuidance(
        tier=AgeTier.MIDDLE_GRADE,
        age_range="9 to 11",
        guidance=(
            "Sentences of at most 22 words. Vary their length.\n"
            "Keep the original's imagery and humour where a child can follow it.\n"
            "Replace archaic or regional words, but keep the period feel.\n"
            "Keep subplots; do not flatten the story to a summary."
        ),
    ),
}


def guidance_for(tier: AgeTier) -> TierGuidance:
    return _GUIDANCE[tier]
