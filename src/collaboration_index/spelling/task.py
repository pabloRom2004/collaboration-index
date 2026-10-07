"""The spelling collaboration task with YAML-owned experiment defaults."""

import json
from pathlib import Path
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample

from collaboration_index.spelling.dataset import deal
from collaboration_index.task import defaults, make_task

CONFIG = defaults("spelling")
ARGS = CONFIG["task"]["args"]


@task
def spelling(
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
    sentences_file: str | None = ARGS["sentences_file"],
    candidate_count: int | None = ARGS["candidate_count"],
    copies: int = ARGS["copies"],
    minimum_hand: int = ARGS["minimum_hand"],
    max_characters: int | None = ARGS["max_characters"],
) -> Task:
    """Build one team attempt with private peers, a common board and shared sandbox."""
    if agents < 1:
        raise ValueError("agents must be positive")
    if max_characters is not None and max_characters < 1:
        raise ValueError("max_characters must be positive or null")
    pool = (
        str(Path(__file__).parents[1] / "assets/spelling_sentences.txt")
        if sentences_file is None
        else sentences_file
    )
    data = deal(pool, agents, seed, copies, minimum_hand, candidate_count)
    data["max_characters"] = max_characters
    sample = Sample(
        id=f"spelling-seed-{seed}",
        input="Spell one of the candidate sentences together, one character at a time. Each agent has a private hand of reusable characters. submit_letter(character) appends a held character to the hidden shared line. Only an agent holding return can end the line by submitting return. No agent can read, remove, edit or reorder that line. The score is edit-distance similarity to the closest shown sentence. Candidates: "
        + json.dumps(data["sentences"]),
        metadata={"data": data, "seed": seed},
    )
    return make_task(
        "spelling",
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
