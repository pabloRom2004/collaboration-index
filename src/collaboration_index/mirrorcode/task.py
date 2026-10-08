"""MirrorCode reimplementation by symmetric peers who share one workspace container."""

import asyncio
import copy
import math
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Epochs, Task, task
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import Generate, Solver, TaskState, chain, solver
from inspect_ai.tool import Tool, ToolDef, ToolResult
from inspect_ai.util import SandboxEnvironmentSpec

from collaboration_index.harness import prepare_team, team_agents
from collaboration_index.task import defaults

CONFIG = defaults("mirrorcode")
ARGS = CONFIG["task"]["args"]

# MirrorCode's resources tool reports sample limits, while each peer's token
# budget is its own subagent limit, so the tool and this line are removed
RESOURCES_LINE = "You can call the `resources` tool to check resource limits (like time or tokens).\n"


SCORING_SERVICES = (
    "reference-scoring",
    "agent-scoring-visible",
    "agent-scoring-hidden",
)
# the scoring pipeline serving the current evaluate_testcases call; 0 is upstream's
PIPELINE: ContextVar[int] = ContextVar("mirrorcode_scoring_pipeline", default=0)
TAR_LOCK: ContextVar[asyncio.Lock | None] = ContextVar(
    "mirrorcode_tar_lock", default=None
)


def route_scoring() -> None:
    """Send MirrorCode's scoring-container lookups to the pipeline chosen for each call."""
    import mc.scorer as scorer

    if getattr(scorer, "team_pipelines_installed", False):
        return
    lookup, read_tar = scorer.sandbox, scorer._read_workspace_tar

    def pipeline_sandbox(name: str = "default") -> Any:
        """Map a scoring service to its copy in the current pipeline."""
        index = PIPELINE.get()
        return lookup(f"{name}-{index}" if index and name in SCORING_SERVICES else name)

    async def read_tar_alone() -> bytes:
        """Pack the workspace one call at a time, since the tar has a fixed path."""
        lock = TAR_LOCK.get()
        if lock is None:
            return await read_tar()
        async with lock:
            return await read_tar()

    scorer.sandbox = pipeline_sandbox
    scorer._read_workspace_tar = read_tar_alone
    scorer.team_pipelines_installed = True


def pooled(
    definition: ToolDef, pipelines: asyncio.Queue[int], tar_lock: asyncio.Lock
) -> Tool:
    """Run each call on a free scoring pipeline so peers never share scoring paths."""

    async def execute() -> ToolResult:
        """Take a free pipeline, run the wrapped tool on it, then hand it back."""
        index = await pipelines.get()
        pipeline, lock = PIPELINE.set(index), TAR_LOCK.set(tar_lock)
        try:
            result: ToolResult = await definition.tool()
            return result
        finally:
            PIPELINE.reset(pipeline)
            TAR_LOCK.reset(lock)
            pipelines.put_nowait(index)

    return ToolDef(
        execute,
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    ).as_tool()


def scaled_workspace(
    compose: Path,
    agents: int,
    memory_per_agent_mb: int,
    cpus_per_agent: float,
    pipelines: int,
) -> Path:
    """Write a copy of MirrorCode's compose file whose workspace and scoring grow with the team."""
    spec = yaml.safe_load(compose.read_text())
    workspace = spec["services"]["default"]
    # upstream sizes the workspace for one agent at 2 GiB; each peer adds its share
    workspace["mem_limit"] = f"{2048 + agents * memory_per_agent_mb}m"
    workspace["cpus"] = 1 + agents * cpus_per_agent
    for index in range(1, pipelines):
        for name in SCORING_SERVICES:
            spec["services"][f"{name}-{index}"] = copy.deepcopy(spec["services"][name])
    # Hawk only converts files whose names end in compose.yaml
    path = compose.with_name(f"team-{agents}-compose.yaml")
    path.write_text(yaml.safe_dump(spec))
    return path


@solver
def shared_workspace(docs: bool, include_source: bool, pipelines: int) -> Solver:
    """Run MirrorCode's workspace setup, then adapt its tools for several peers."""
    from mc.task import setup_solver

    setup = setup_solver(
        docs=docs,
        include_source=include_source,
        visible_case_ablation=0.0,
        visible_case_ablation_seed=0,
        evaluate_testcases_tool=True,
    )

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        """Install the upstream files and tools, then give scoring calls separate pipelines."""
        state = await setup(state, generate)
        # evaluate_testcases reuses fixed tar and source paths in the scoring
        # containers, so two calls may only run at once on different pipelines
        route_scoring()
        free: asyncio.Queue[int] = asyncio.Queue()
        for index in range(pipelines):
            free.put_nowait(index)
        tar_lock = asyncio.Lock()
        tools = []
        for item in state.tools:
            definition = ToolDef(item)
            if definition.name == "resources":
                continue
            tools.append(
                pooled(definition, free, tar_lock)
                if definition.name == "evaluate_testcases"
                else item
            )
        state.tools = tools
        return state

    return solve


@task
def mirrorcode(
    agents: int = ARGS["agents"],
    token_limit_per_agent: int | None = ARGS["token_limit_per_agent"],
    team_time_limit: float | None = ARGS["team_time_limit"],
    agent: str = ARGS["agent"],
    agent_args: dict[str, Any] = ARGS["agent_args"],
    artifact_dir: str = ARGS["artifact_dir"],
    compaction_threshold: float = ARGS["compaction_threshold"],
    context_window: int | None = ARGS["context_window"],
    target: str = ARGS["target"],
    language: str = ARGS["language"],
    docs: bool = ARGS["docs"],
    include_source: bool = ARGS["include_source"],
    reference_binary: bool = ARGS["reference_binary"],
    memory_per_agent_mb: int = ARGS["memory_per_agent_mb"],
    cpus_per_agent: float = ARGS["cpus_per_agent"],
    agents_per_scoring_pipeline: int = ARGS["agents_per_scoring_pipeline"],
) -> Task:
    """Build one team attempt at reimplementing a MirrorCode target in one shared workspace."""
    from mc import AgentImplementationLanguage, TargetProgram
    from mc.scorer import mirrorcode_scorer
    from mc.task import get_sample

    if type(agents) is not int or not 1 <= agents <= 64:
        raise ValueError("MirrorCode team sizes must be between 1 and 64")
    if token_limit_per_agent is not None and token_limit_per_agent < 1:
        raise ValueError(
            "token_limit_per_agent must be positive, or null for no per-peer budget"
        )
    if team_time_limit is not None and team_time_limit <= 0:
        raise ValueError("team_time_limit must be positive seconds, or null for none")
    if type(agents_per_scoring_pipeline) is not int or agents_per_scoring_pipeline < 1:
        raise ValueError("agents_per_scoring_pipeline must be a positive integer")
    pipelines = math.ceil(agents / agents_per_scoring_pipeline)
    languages = {item.value.lower(): item for item in AgentImplementationLanguage}
    if language not in languages:
        raise ValueError("language must be one of: " + ", ".join(languages))
    # a submit gate needs one shared token budget, which teams do not have
    sample = get_sample(
        TargetProgram(target),
        languages[language],
        docs=docs,
        include_source=include_source,
        gated_submit=None,
        visible_case_ablation=0.0,
        evaluate_testcases_tool=True,
        reference_binary=reference_binary,
    )
    if not isinstance(sample.input, str) or RESOURCES_LINE not in sample.input:
        raise RuntimeError("MirrorCode's task description no longer matches")
    sample.input = sample.input.replace(RESOURCES_LINE, "")
    if (
        not isinstance(sample.sandbox, SandboxEnvironmentSpec)
        or not sample.sandbox.config
    ):
        raise RuntimeError("MirrorCode's sample no longer carries its compose file")
    sample.sandbox = SandboxEnvironmentSpec(
        "docker",
        str(
            scaled_workspace(
                Path(str(sample.sandbox.config)),
                agents,
                memory_per_agent_mb,
                cpus_per_agent,
                pipelines,
            )
        ),
    )
    sample.metadata = {
        "data": {},
        "sandbox_enabled": True,
        "answer_judge": "mirrorcode_scorer",
        "benchmark_title": "MirrorCode",
    }
    return Task(
        dataset=[sample],
        setup=chain(
            prepare_team("mirrorcode", agents, "collaborative", artifact_dir),
            shared_workspace(docs, include_source, pipelines),
        ),
        solver=team_agents(
            token_limit_per_agent,
            team_time_limit,
            agent,
            agent_args,
            compaction_threshold,
            context_window,
        ),
        scorer=mirrorcode_scorer(visible_case_ablation=0.0),
        config=GenerateConfig(**CONFIG["generate_config"]),
        # MirrorCode's metrics read every epoch's score rather than a reduced mean
        epochs=Epochs(CONFIG["eval_config"]["epochs"], reducer=[]),
        version=1,
        name="mirrorcode",
        display_name="MirrorCode",
        metadata={
            "team_sample": True,
            "condition": "collaborative",
            "agents": agents,
            "benchmark_variant": "collaboration_index_v1",
            "token_limit_per_agent": token_limit_per_agent,
            "planned_team_token_budget": None
            if token_limit_per_agent is None
            else agents * token_limit_per_agent,
            "context_window": context_window,
            "mirrorcode_target": target,
            "mirrorcode_language": language,
            "scoring_pipelines": pipelines,
        },
    )
