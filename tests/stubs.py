"""Test doubles shared across pipeline tests."""

from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall


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


@component
class ScriptedChatGenerator:
    """Replays a fixed script of assistant turns.

    Each entry is either a string (a text reply) or a list of
    ``(tool_name, arguments)`` pairs to emit as tool calls. When the script runs
    out, the last entry repeats forever -- which is how a model that never
    terminates is simulated.
    """

    def __init__(self, script: list[object]) -> None:
        self.script = script
        self.calls = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self,
        messages: list[ChatMessage],
        tools: object = None,
        **kwargs: object,
    ) -> dict[str, list[ChatMessage]]:
        # `tools` must be an explicit parameter: Agent inspects the signature
        # and refuses a generator that only accepts it via **kwargs.
        turn = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1

        if isinstance(turn, str):
            return {"replies": [ChatMessage.from_assistant(turn)]}

        assert isinstance(turn, list)
        tool_calls = [
            ToolCall(tool_name=name, arguments=dict(args), id=f"call_{self.calls}_{i}")
            for i, (name, args) in enumerate(turn)
        ]
        return {"replies": [ChatMessage.from_assistant(tool_calls=tool_calls)]}


def reads(count: int) -> list[object]:
    """A script of ``count`` read_next_chunk turns."""
    return [[("read_next_chunk", {})] for _ in range(count)]


def decision(**arguments: object) -> list[object]:
    """A single record_decision turn."""
    return [[("record_decision", arguments)]]
