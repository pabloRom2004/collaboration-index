"""A starter benchmark using the installed common team infrastructure."""

from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Task, task

from collaboration_index.hle import hle_collaboration

ROOT = Path(__file__).parent
CONFIG = yaml.safe_load((ROOT / "run_configs/default.yaml").read_text())
ARGS = CONFIG["task"]["args"]


@task
def toy_exam(
    title: str = ARGS["title"],
    agents: int = ARGS["agents"],
    condition: str = ARGS["condition"],
    seed: int = ARGS["seed"],
    token_limit_per_agent: int | None = ARGS["token_limit_per_agent"],
    team_time_limit: float | None = ARGS["team_time_limit"],
    agent: str = ARGS["agent"],
    agent_args: dict[str, Any] = ARGS["agent_args"],
    artifact_dir: str = ARGS["artifact_dir"],
    sandbox_enabled: bool = ARGS["sandbox_enabled"],
    sandbox_type: str = ARGS["sandbox_type"],
    compaction_threshold: float = ARGS["compaction_threshold"],
    context_window: int | None = ARGS["context_window"],
    records_file: str = ARGS["records_file"],
    answer_characters: int = ARGS["answer_characters"],
    answer_judge: str = ARGS["answer_judge"],
    max_grader_attempts: int = ARGS["max_grader_attempts"],
) -> Task:
    """Replace the question data while retaining the board, shared sandbox and scoring contract."""
    records = Path(records_file)
    if not records.is_absolute():
        records = ROOT / records
    return hle_collaboration(
        title=title,
        agents=agents,
        condition=condition,
        seed=seed,
        token_limit_per_agent=token_limit_per_agent,
        team_time_limit=team_time_limit,
        agent=agent,
        agent_args=agent_args,
        artifact_dir=artifact_dir,
        sandbox_enabled=sandbox_enabled,
        sandbox_type=sandbox_type,
        compaction_threshold=compaction_threshold,
        context_window=context_window,
        records_file=str(records),
        answer_characters=answer_characters,
        answer_judge=answer_judge,
        max_grader_attempts=max_grader_attempts,
    )
