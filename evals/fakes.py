"""A scripted generator for ``--dry-run``.

It answers each fixture with a decision drawn from that case's allowed set, so
a dry run exercises loading, comparison, table rendering and the exit code. It
proves the harness runs. It proves nothing about the model, and the runner says
so in its own output rather than leaving that to be inferred.
"""

from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall

from evals.cases import CASES
from gutenberg_simplifier.boundaries import RejectReason

#: Recognisable text from each fixture, mapped to the decision to return.
_SIGNATURES: tuple[tuple[str, str], ...] = tuple(
    (marker, case_name)
    for marker, case_name in (
        ("A DAMAGED SCAN", "ocr_garbled"),
        ("BEEKEEPING APPARATUS", "not_a_story"),
        ("AN UNFINISHED TALE", "truncated_story"),
        ("A TALE BEHIND MANY PAGES", "heavy_front_matter"),
        ("THE RABBIT AND THE CARROT", "clean_story"),
    )
)

_CASES_BY_NAME = {case.name: case for case in CASES}


@component
class ScriptedEvalGenerator:
    """Reads the first chunk, then records that case's expected decision."""

    @component.output_types(replies=list[ChatMessage])
    def run(
        self,
        messages: list[ChatMessage],
        streaming_callback: object = None,
        generation_kwargs: object = None,
        tools: object = None,
    ) -> dict[str, list[ChatMessage]]:
        transcript = _transcript(messages)

        # Keyed on fixture content, not on the tool name: the tool name appears
        # in the system prompt, so testing for it decided before reading a line.
        case_name = _identify(transcript)
        if case_name is None:
            return {"replies": [_call("read_next_chunk", {})]}

        return {"replies": [_call("record_decision", _arguments(case_name))]}


def _transcript(messages: list[ChatMessage]) -> str:
    """Flatten a conversation, including tool results.

    Chunk text arrives as a tool *result*, where ChatMessage.text is None --
    reading only .text sees the prompts and never the book.
    """
    parts: list[str] = []
    for message in messages:
        if message.text:
            parts.append(message.text)
        for tool_result in message.tool_call_results or []:
            # ToolCallResult.result is typed as str | Sequence[content blocks];
            # the chunk reader only ever returns a plain string.
            if isinstance(tool_result.result, str):
                parts.append(tool_result.result)
    return "\n".join(parts)


def _identify(transcript: str) -> str | None:
    for marker, case_name in _SIGNATURES:
        if marker in transcript:
            return case_name
    return None


def _arguments(case_name: str) -> dict[str, object]:
    allowed = _CASES_BY_NAME[case_name].allowed
    # Prefer a rejection when the case expects one; otherwise report a find.
    reason = next((r for r in allowed if isinstance(r, RejectReason)), None)
    if reason is not None and None not in allowed:
        return {"found": False, "reject_reason": reason.value, "notes": f"scripted: {case_name}"}
    return {
        "found": True,
        "start_line": 0,
        "end_line": 5,
        "confidence": "high",
        "notes": f"scripted: {case_name}",
    }


def _call(name: str, arguments: dict[str, object]) -> ChatMessage:
    return ChatMessage.from_assistant(
        tool_calls=[ToolCall(tool_name=name, arguments=arguments, id=f"c_{name}")]
    )
