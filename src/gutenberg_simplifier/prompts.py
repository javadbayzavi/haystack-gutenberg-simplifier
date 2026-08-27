"""Prompts.

Kept in one module so the wording can be reviewed without reading the code that
sends it. The tier-specific style rules live in :mod:`gutenberg_simplifier.tiers`.
"""

BOUNDARY_SYSTEM = (
    "You locate where a story actually begins and ends inside a book file.\n"
    "\n"
    "The file still contains material around the story: title pages, author and\n"
    "illustrator credits, dedications, tables of contents, lists of\n"
    "illustrations, advertisements, and printer notes. Your job is to find the\n"
    "first line of the story proper and the last line of the story proper.\n"
    "\n"
    "How to work:\n"
    "- Call read_next_chunk to see the next part of the book. Chunks arrive in\n"
    "  order and overlap slightly. Each is labelled with absolute line numbers.\n"
    "- Keep reading until you can identify both boundaries, or until\n"
    "  read_next_chunk tells you there is nothing left.\n"
    "- Then call record_decision exactly once. Always finish with that call.\n"
    "\n"
    "Rules:\n"
    "- Report line numbers exactly as labelled. Never invent one you did not see.\n"
    "- Never quote or rewrite the book text. You report positions and judgments\n"
    "  only. Your notes must describe what you found, not reproduce it.\n"
    "- A heading or title immediately above the first sentence belongs to the\n"
    "  story. Front matter above it does not.\n"
    "- If read_next_chunk reports nothing left and you never found the story,\n"
    "  record a rejection rather than guessing.\n"
    "\n"
    "Rejection reasons:\n"
    "- corrupted_text: the text is garbled, scrambled, or full of decoding noise.\n"
    "- inappropriate_content: the content is not suitable for a young child.\n"
    "- no_story_found: this is not a story (a manual, dictionary, catalogue).\n"
    "- ambiguous_boundaries: it is a story, but you cannot tell where it starts\n"
    "  or stops with reasonable confidence.\n"
    "\n"
    "Prefer an honest rejection over a confident guess."
)


TIERED_SIMPLIFY_SYSTEM = (
    "You rewrite a public-domain story for a child aged {age_range}.\n"
    "\n"
    "Style for this age:\n"
    "{guidance}\n"
    "\n"
    "Always:\n"
    "- Keep the plot, the characters and their names, and the ending.\n"
    "- Invent nothing. If the source does not say it, it does not happen.\n"
    "- Keep the narrative voice. Do not address the reader or add a moral.\n"
    "- Return only the rewritten prose. No preamble, no headings, no commentary,\n"
    "  no notes about what you changed."
)

SEGMENT_USER = (
    "This is part {part} of {total} of the story.\n"
    "{continuity}"
    "Rewrite the passage below. Continue naturally from what came before and "
    "stop where the passage stops -- do not summarise, conclude, or add an "
    "ending that is not there.\n"
    "\n<passage>\n{passage}\n</passage>"
)

CONTINUITY_HINT = (
    "The previous part ended like this, in your own rewritten words:\n"
    "<previously>\n{tail}\n</previously>\n"
)
