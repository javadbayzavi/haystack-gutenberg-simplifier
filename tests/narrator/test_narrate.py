"""Planning and streaming narration."""

import pytest

from gutenberg_narrator.audio import AudioFormat
from gutenberg_narrator.errors import TextTooLongError
from gutenberg_narrator.narrate import narrate, plan_narration
from gutenberg_narrator.stub import StubEngine
from gutenberg_narrator.voices import voice_for

TEXT = "Once upon a time there was a rabbit. He lived under a hedge. He ate a carrot."


def test_planning_reports_the_cost_before_any_synthesis() -> None:
    """The service needs this to reject with a status code, before the stream
    starts and takes status codes away from it."""
    engine = StubEngine()

    plan = plan_narration(TEXT, voice_for("preschool"), engine)

    assert plan.unit_count >= 1
    assert plan.characters > 0
    assert engine.spoken == []  # nothing was synthesised


def test_an_oversized_request_is_refused_without_calling_the_engine() -> None:
    engine = StubEngine()

    with pytest.raises(TextTooLongError) as caught:
        plan_narration("x" * 500, voice_for(None), engine, max_total_characters=100)

    assert caught.value.max_characters == 100
    assert engine.spoken == []


def test_audio_is_yielded_per_unit_not_as_one_blob() -> None:
    """If it arrives whole, nothing can play until synthesis finishes."""
    engine = StubEngine()
    plan = plan_narration(TEXT, voice_for(None), engine, max_characters=30)

    chunks = list(narrate(plan, engine))

    assert plan.unit_count > 1
    assert len(chunks) > plan.unit_count - 1


def test_a_pause_separates_units_but_never_ends_the_stream() -> None:
    """Trailing silence sounds like the file failed rather than finished."""
    engine = StubEngine()
    voice = voice_for("preschool")
    plan = plan_narration(TEXT, voice, engine, max_characters=30)

    chunks = list(narrate(plan, engine))
    pause_bytes = plan.audio_format.bytes_for(voice.pause_seconds)

    assert len(chunks) == plan.unit_count * 2 - 1
    assert len(chunks[-1]) != pause_bytes or chunks[-1] != b"\x00" * pause_bytes
    assert len(chunks[1]) == pause_bytes  # the separator sits between units


def test_the_engine_receives_exactly_the_planned_units() -> None:
    engine = StubEngine()
    plan = plan_narration(TEXT, voice_for(None), engine, max_characters=30)

    list(narrate(plan, engine))

    assert [text for text, _ in engine.spoken] == list(plan.units)


def test_the_voice_reaches_the_engine() -> None:
    engine = StubEngine()
    voice = voice_for("middle_grade")
    plan = plan_narration(TEXT, voice, engine)

    list(narrate(plan, engine))

    assert all(sent is voice for _, sent in engine.spoken)


def test_a_slower_pace_produces_longer_audio() -> None:
    engine = StubEngine()
    slow = plan_narration(TEXT, voice_for("preschool"), engine)
    fast = plan_narration(TEXT, voice_for("middle_grade"), engine)

    slow_bytes = sum(len(chunk) for chunk in narrate(slow, engine))
    fast_bytes = sum(len(chunk) for chunk in narrate(fast, engine))

    assert slow_bytes > fast_bytes


def test_empty_text_produces_no_audio_and_no_calls() -> None:
    engine = StubEngine()
    plan = plan_narration("   ", voice_for(None), engine)

    assert plan.unit_count == 0
    assert list(narrate(plan, engine)) == []
    assert engine.spoken == []


def test_the_plan_carries_the_engine_format() -> None:
    """Mixing formats mid-stream plays at the wrong speed rather than failing."""
    engine = StubEngine(AudioFormat(sample_rate=16_000))

    assert plan_narration(TEXT, voice_for(None), engine).audio_format.sample_rate == 16_000
