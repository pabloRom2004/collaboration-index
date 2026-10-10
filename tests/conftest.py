"""Keep disposable test logs separate from concurrently collected real evaluations."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_mock_logs(request, tmp_path: Path, monkeypatch) -> None:
    """Redirect a test module's repository log directory into its own temporary folder."""
    logs = Path(__file__).parents[1] / "logs"
    if getattr(request.module, "LOGS", None) == logs:
        monkeypatch.setattr(request.module, "LOGS", tmp_path / "logs")
