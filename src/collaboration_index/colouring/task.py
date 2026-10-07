"""The graph colouring collaboration task with YAML-owned experiment defaults."""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample

from collaboration_index.colouring.dataset import draw_network
from collaboration_index.task import defaults, make_task

CONFIG = defaults("colouring")
ARGS = CONFIG["task"]["args"]


@task
def colouring(
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
    colours: int = ARGS["colours"],
    topology: str = ARGS["topology"],
    degree: float = ARGS["degree"],
) -> Task:
    """Build one team attempt where every peer is a node that only DMs its neighbours."""
    if agents < 1:
        raise ValueError("agents must be positive")
    data = draw_network(agents, seed, colours, topology, degree)
    names = ", ".join(data["colours"])
    sample = Sample(
        id=f"colouring-{topology}-k{colours}-seed-{seed}",
        input=f"Colour a hidden network together. Every agent is one node and knows only its own neighbours. The goal is for every agent to hold one of these colours: {names}, with no two neighbours holding the same colour. The moment that is true everywhere, the attempt ends for everyone. set_colour(colour) sets or changes your own colour at any time. Nobody can see any colour, including a neighbour's. The score is the percentage of network edges whose two ends hold different colours at the end; an uncoloured end counts as a clash.",
        metadata={"data": data, "seed": seed},
    )
    return make_task(
        "colouring",
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
        context_window=context_window,
    )
