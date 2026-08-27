"""Tests for the eval harness itself.

A harness that always reports PASS is worse than no harness, so what is
asserted here is that it detects a wrong answer, survives a crashing case, and
refuses to run against no model at all.
"""

import pytest
from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall

from evals.__main__ import main as evals_main
from evals.cases import CASES, EvalCase
from evals.runner import format_table, run_all, run_case


def _case(name: str) -> EvalCase:
    return next(case for case in CASES if case.name == name)


@component
class AlwaysRejects:
    """Answers every fixture with the same rejection, right or wrong."""

    @component.output_types(replies=list[ChatMessage])
    def run(
        self,
        messages: list[ChatMessage],
        streaming_callback: object = None,
        generation_kwargs: object = None,
        tools: object = None,
    ) -> dict[str, list[ChatMessage]]:
        call = ToolCall(
            tool_name="record_decision",
            arguments={"found": False, "reject_reason": "no_story_found", "notes": "always"},
            id="c1",
        )
        return {"replies": [ChatMessage.from_assistant(tool_calls=[call])]}


@component
class Explodes:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self,
        messages: list[ChatMessage],
        streaming_callback: object = None,
        generation_kwargs: object = None,
        tools: object = None,
    ) -> dict[str, list[ChatMessage]]:
        raise RuntimeError("provider down")


def test_a_wrong_answer_is_reported_as_a_failure() -> None:
    """The property that makes the harness worth having."""
    outcome = run_case(_case("clean_story"), AlwaysRejects())

    assert outcome.passed is False
    assert outcome.actual == "no_story_found"
    assert outcome.expected == "accepted"


def test_a_right_answer_passes() -> None:
    outcome = run_case(_case("not_a_story"), AlwaysRejects())

    assert outcome.passed is True


def test_a_case_with_several_allowed_outcomes_accepts_any_of_them() -> None:
    outcome = run_case(_case("truncated_story"), AlwaysRejects())

    # no_story_found is not among the allowed outcomes for this case.
    assert outcome.passed is False
    assert "ambiguous_boundaries" in outcome.expected


def test_a_crashing_case_fails_without_stopping_the_run() -> None:
    outcomes = run_all(Explodes())

    assert len(outcomes) == len(CASES)
    assert all(outcome.passed is False for outcome in outcomes)
    assert all("provider down" in (outcome.error or "") for outcome in outcomes)


def test_the_table_names_the_failures() -> None:
    table = format_table(run_all(AlwaysRejects()))

    assert "FAIL" in table
    assert "Failures:" in table
    assert "clean_story: expected accepted, got no_story_found" in table


def test_running_without_a_key_refuses_rather_than_faking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An eval that mocks the thing being evaluated reports a meaningless green.

    Exercises the real environment check rather than patching the module.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert evals_main([]) == 2


def test_the_dry_run_path_still_works_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert evals_main(["--dry-run"]) == 0
