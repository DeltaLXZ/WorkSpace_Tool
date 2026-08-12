from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def clean_dir() -> Path:
    return FIXTURES / "clean"


@pytest.fixture
def broken_dir() -> Path:
    return FIXTURES / "broken"
