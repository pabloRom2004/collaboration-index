"""The counting collaboration task with YAML-owned experiment defaults."""

import math
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample

from collaboration_index.task import defaults, make_task

CONFIG = defaults("counting")
ARGS = CONFIG["task"]["args"]


@task
def counting(
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
    target: int | None = ARGS["target"],
    submissions_per_agent: int | None = ARGS["submissions_per_agent"],
) -> Task:
    """Build one team attempt with private peers, a common board and shared sandbox."""
    if agents < 1:
        raise ValueError("agents must be positive")
    goal = 2 * agents if target is None else target
    if type(goal) is not int or goal < 1:
        raise ValueError("target must be positive, or null for two numbers per agent")
    quota = (
        math.ceil(goal / agents)
        if submissions_per_agent is None
        else submissions_per_agent
    )
    if type(quota) is not int or quota < 1 or quota * agents < goal:
        raise ValueError("The per-agent quota must permit the team to reach its target")
    sample = Sample(
        id=f"counting-{goal}-seed-{seed}",
        input=f"Count together from 1 to {goal} in exact order. submit_number(number) appends one integer to a hidden shared list. No agent can read, edit, delete or reorder it. Incorrect numbers remain and occupy slots. Each agent has a certain budget of max accepted submissions, you need to figure out what this is.",
        metadata={"data": {"target": goal, "quota": quota}, "seed": seed},
    )
    return make_task(
        "counting",
        sample,
        CONFIG,
        agents,
        condition,
        token_limit_per_agent,
        team_time_limit,
        agent,
        agent_args,
        artifact_dir,
        sandbox_enabled,
        sandbox_type,
        "exact",
        1,
        compaction_threshold,
    )
