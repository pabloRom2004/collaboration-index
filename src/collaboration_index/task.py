"""Compose the common team environment without placing answer keys in participant inputs."""

from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Epochs, Task
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig

from collaboration_index.harness import prepare_team, team_agents
from collaboration_index.scorers import team_score


def defaults(benchmark: str) -> dict[str, Any]:
    """Load the packaged YAML as the single source of a task's public defaults."""
    return yaml.safe_load(
        (Path(__file__).parent / benchmark / "run_configs/default.yaml").read_text()
    )


def make_task(
    benchmark: str,
    sample: Sample,
    config: dict[str, Any],
    agents: int,
    condition: str,
    token_limit_per_agent: int | None,
    team_time_limit: float | None,
    agent: str,
    agent_args: dict[str, Any],
    artifact_dir: str,
    sandbox_enabled: bool,
    answer_judge: str,
    max_grader_attempts: int,
    compaction_threshold: float,
    title: str | None = None,
) -> Task:
    """Wire invariant setup, replaceable agent, shared sandbox and authoritative scoring."""
    if type(agents) is not int or not 1 <= agents <= 32:
        raise ValueError("Prototype team sizes must be between 1 and 32")
    if condition not in {"collaborative", "oracle_allocation"}:
        raise ValueError("Unknown collaboration condition")
    if token_limit_per_agent is None or token_limit_per_agent < 1:
        raise ValueError(
            "Provide a positive token_limit_per_agent before running this task"
        )
    sample.metadata = dict(
        sample.metadata or {},
        sandbox_enabled=sandbox_enabled,
        answer_judge=answer_judge,
        benchmark_title=title
        or {
            "hle": "HLE collaboration",
            "counting": "Counting",
            "spelling": "Spelling",
            "colouring": "Graph colouring",
        }[benchmark],
    )
    return Task(
        dataset=[sample],
        setup=prepare_team(benchmark, agents, condition, artifact_dir),
        solver=team_agents(
            token_limit_per_agent,
            team_time_limit,
            agent,
            agent_args,
            compaction_threshold,
        ),
        scorer=team_score(answer_judge, max_grader_attempts),
        sandbox=("docker", str(Path(__file__).parent / "assets/sandbox/compose.yaml"))
        if sandbox_enabled
        else None,
        config=GenerateConfig(**config["generate_config"]),
        epochs=Epochs(config["eval_config"]["epochs"], "mean"),
        # Limits belong to each subagent. A second sample limit can interrupt
        # team finalization when simultaneous peers exhaust their own budgets.
        version=1,
        name=benchmark,
        display_name=sample.metadata["benchmark_title"],
        metadata={
            "team_sample": True,
            "condition": condition,
            "agents": agents,
            "benchmark_variant": "collaboration_index_v1",
            "token_limit_per_agent": token_limit_per_agent,
            "planned_team_token_budget": agents * token_limit_per_agent,
        },
    )
