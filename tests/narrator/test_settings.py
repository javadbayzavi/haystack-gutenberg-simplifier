"""Engine selection from the environment."""

import pytest

from gutenberg_narrator import settings
from gutenberg_narrator.hosted import HostedEngine
from gutenberg_narrator.stub import StubEngine


def test_the_default_engine_is_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment that forgets to choose produces silence, not a bill."""
    monkeypatch.delenv("NARRATOR_ENGINE", raising=False)

    assert isinstance(settings.build_engine(), StubEngine)


def test_an_unknown_engine_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo should fail the deployment, not every request."""
    monkeypatch.setenv("NARRATOR_ENGINE", "chatterbox")

    with pytest.raises(ValueError, match="Unknown NARRATOR_ENGINE"):
        settings.build_engine()


def test_the_hosted_engine_refuses_to_start_without_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pod that starts without its key and then 500s is worse than one that
    never starts."""
    monkeypatch.setenv("NARRATOR_ENGINE", "hosted")
    monkeypatch.delenv("TTS_API_KEY", raising=False)

    with pytest.raises(ValueError, match="requires TTS_API_KEY"):
        settings.build_engine()


def test_the_hosted_engine_builds_with_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATOR_ENGINE", "hosted")
    monkeypatch.setenv("TTS_API_KEY", "sk-test")

    engine = settings.build_engine()
    try:
        assert isinstance(engine, HostedEngine)
        assert engine.audio_format.sample_rate == 24_000
    finally:
        engine.close()


def test_voice_names_can_be_overridden_per_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The right provider voice for a children's narrator is a deploy-time
    decision, not a code change."""
    monkeypatch.setenv("NARRATOR_ENGINE", "hosted")
    monkeypatch.setenv("TTS_API_KEY", "sk-test")
    monkeypatch.setenv("TTS_VOICE_PRESCHOOL", "shimmer")

    engine = settings.build_engine()
    try:
        assert engine._voice_names["preschool"] == "shimmer"  # type: ignore[attr-defined]
    finally:
        engine.close()


def test_limits_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATOR_MAX_TOTAL_CHARACTERS", "1234")
    monkeypatch.setenv("NARRATOR_MAX_UNIT_CHARACTERS", "99")

    assert settings.max_total_characters() == 1234
    assert settings.max_unit_characters() == 99
