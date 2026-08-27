"""Test doubles shared across pipeline tests."""

from haystack import component
from haystack.dataclasses import ChatMessage


@component
class StubChatGenerator:
    """Stands in for AnthropicChatGenerator: no API key, no network.

    Records the messages it was called with so tests can assert what actually
    reached the model.
    """

    def __init__(self, reply: str = "A rabbit ate a carrot. The end.") -> None:
        self.reply = reply
        self.seen: list[list[ChatMessage]] = []

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage]) -> dict[str, list[ChatMessage]]:
        self.seen.append(messages)
        return {"replies": [ChatMessage.from_assistant(self.reply)]}


@component
class EmptyChatGenerator:
    """A generator that returns nothing useful, to exercise the empty-reply path."""

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage]) -> dict[str, list[ChatMessage]]:
        return {"replies": []}
