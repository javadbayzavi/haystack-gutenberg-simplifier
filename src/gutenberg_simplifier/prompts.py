"""Prompts for the naive simplification step.

Naive is the point at this stage: one prompt over the whole book body, no
chunking, no age tiers, no boundary detection. It exists so there is a complete
path from HTTP request to generated text to deploy against. PR 4 replaces it
with tiered, chunked simplification.
"""

from haystack.dataclasses import ChatMessage

SIMPLIFY_SYSTEM = (
    "You rewrite public-domain stories so a young child can follow them.\n"
    "\n"
    "Rules:\n"
    "- Keep the plot, characters, and ending. Do not invent events.\n"
    "- Use short sentences and everyday words.\n"
    "- Keep the narrative voice; do not address the reader or add a moral.\n"
    "- Drop front matter, illustration captions, and publisher notes.\n"
    "- Return only the rewritten story, with no preamble or commentary."
)

SIMPLIFY_USER = "Rewrite this story for a young child.\n\n<story>\n{{ story }}\n</story>"


def simplification_template() -> list[ChatMessage]:
    """The chat template consumed by ``ChatPromptBuilder``."""
    return [
        ChatMessage.from_system(SIMPLIFY_SYSTEM),
        ChatMessage.from_user(SIMPLIFY_USER),
    ]
