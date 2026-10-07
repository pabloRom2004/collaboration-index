"""Remove only owned mock logs after each test while preserving verification artifacts."""

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_mock_logs() -> Iterator[None]:
    """Track exact new .eval files in the repository's flat test log directory."""
    logs = Path(__file__).parents[1] / "logs"
    logs.mkdir(exist_ok=True)
    before = set(logs.glob("*.eval"))
    yield
    for path in set(logs.glob("*.eval")) - before:
        path.unlink()
