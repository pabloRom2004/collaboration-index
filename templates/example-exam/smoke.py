"""Verify the starter through the shared mock/sandbox/board/replay path."""

import json
from pathlib import Path
from typing import Any, cast

from inspect_ai import eval as inspect_eval
from task import toy_exam

from collaboration_index.replay import render
from collaboration_index.smoke import fixture_model


def main() -> None:
    """Run a harmless two-peer exam and retain its portable replay and verification receipt."""
    root = Path(__file__).parent
    task = toy_exam(
        token_limit_per_agent=10000,
        team_time_limit=30,
        artifact_dir=str(root / "run-artifacts"),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(root / "logs"), display="none"
    )
    if log.status != "success" or not log.samples or log.samples[0].error:
        raise RuntimeError("Starter smoke failed; its authored mock log was retained")
    sample = log.samples[0]
    metrics = cast(dict[str, Any], (sample.scores or {})["team_score"].value)
    assert metrics["quality"] == 1
    directory = Path(sample.store["TeamHistory:artifact_dir"])
    render(
        Path(log.location),
        directory / "board.jsonl",
        root / "run-artifacts/replay.html",
    )
    (root / "run-artifacts/verification.json").write_text(json.dumps(metrics, indent=2))
    Path(log.location).unlink()
    print("Starter shared-sandbox smoke passed; replay: run-artifacts/replay.html")


if __name__ == "__main__":
    main()
