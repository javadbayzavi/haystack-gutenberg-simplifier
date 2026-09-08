"""Typed failures for narration."""


class GutenbergNarratorError(Exception):
    """Base class for every error this package raises deliberately."""


class UnknownVoiceError(GutenbergNarratorError):
    """No voice profile matches the requested name."""

    def __init__(self, requested: str, available: tuple[str, ...]) -> None:
        super().__init__(f"Unknown voice {requested!r}. Available: {', '.join(available)}")
        self.requested = requested
        self.available = available


class TextTooLongError(GutenbergNarratorError):
    """The request exceeds the total synthesis budget.

    A cheap rejection before any engine is called, the same shape as the
    simplifier's size gate: refusing a 400,000 character request costs nothing,
    while synthesising it costs minutes of compute.
    """

    def __init__(self, characters: int, max_characters: int) -> None:
        super().__init__(
            f"Text is {characters} characters, over the {max_characters} character budget"
        )
        self.characters = characters
        self.max_characters = max_characters


class EngineError(GutenbergNarratorError):
    """The synthesis engine failed.

    Distinct from the errors above because it says nothing about the request --
    the caller cannot fix it by sending different text.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Synthesis failed: {detail}")
        self.detail = detail
