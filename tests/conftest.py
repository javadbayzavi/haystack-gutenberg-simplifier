from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text() -> Callable[[str], str]:
    """Read a checked-in sample by file name."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _read
