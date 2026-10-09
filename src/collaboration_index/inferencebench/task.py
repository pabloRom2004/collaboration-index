"""Compose the pinned InferenceBench workload with the shared native peer harness."""

import copy
import importlib
import math
from pathlib import Path
from typing import Any

from inspect_ai import Epochs, Task, task
from inspect_ai.model import GenerateConfig, get_model
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import SandboxEnvironmentSpec

from collaboration_index.harness import prepare_team, team_agents
from collaboration_index.inferencebench.backend.metrics import (
    aggregate_speedup,
    scored_attempts,
    unscored_attempts,
)
from collaboration_index.inferencebench.resource import allocate, cleanup
from collaboration_index.inferencebench.state import InferenceHistory
from collaboration_index.inferencebench.tools import evaluate, workspace_tools
from collaboration_index.state import TeamHistory
from collaboration_index.task import defaults

CONFIG = defaults("inferencebench")
ARGS = CONFIG["task"]["args"]


def validate_workload(workload: dict[str, Any]) -> None:
    """Reject malformed workload settings before allocating a paid GPU."""
    if set(workload) != set(ARGS["workload"]):
        raise ValueError("workload must contain exactly the documented workload fields")
    counts = [
        "max_model_len",
        "quality_samples",
        "quality_concurrency",
        "quality_baseline_max_attempts",
        "server_wait_seconds",
        "request_timeout_seconds",
    ]
    if any(type(workload[k]) is not int or workload[k] <= 0 for k in counts):
        raise ValueError(
            "Workload counts and durations must be positive integer values"
        )
    limit = workload["request_limit"]
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("request_limit must be a positive integer or null")
    if type(workload["quality_seed"]) is not int or workload["quality_seed"] < 0:
        raise ValueError("quality_seed must be a nonnegative integer")
    if workload["baseline_dtype"] not in {"float16", "bfloat16", "float32"}:
        raise ValueError("Unsupported baseline_dtype")
    if workload["quality_reference_backend"] not in {"transformers", "vllm"}:
        raise ValueError("Unsupported quality_reference_backend")
    tau = workload["quality_tau"]
    if type(tau) not in {int, float} or not math.isfinite(tau) or not 0 < tau <= 1:
        raise ValueError("quality_tau must be finite and in (0, 1]")
    if type(workload["strict_prompt"]) is not bool:
        raise ValueError("strict_prompt must be a boolean")
    if workload["config_defaults"] not in {"default", "original"}:
        raise ValueError("config_defaults must be default or original")


@solver
def prepare_inference_team(
    agents: int,
    artifact_dir: str,
    tool_timeout: int,
    web_search_args: dict[str, Any],
    grader_role: str,
    external: bool,
) -> Solver:
    """Require an explicit integrity judge and prepare one GPU before releasing any peers."""
    from collaboration_index.inferencebench.backend.environment import (
        prepare_environment,
    )
    from collaboration_index.inferencebench.backend.tools import web_search

    upstream = prepare_environment()
    team = prepare_team("inferencebench", agents, "collaborative", artifact_dir)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        """Measure trusted baselines once, initialize the board state and install shared tools."""
        get_model(role=grader_role, required=True)
        state.store_as(InferenceHistory)
        state.store.set(
            "inferencebench_artifacts_root", state.metadata["artifacts_root"]
        )
        if external:
            await allocate(state)
        try:
            state = await upstream(state, generate)
            state = await team(state, generate)
            state.tools = [
                *workspace_tools(tool_timeout, external),
                evaluate(tool_timeout),
                web_search(**web_search_args),
            ]
        except BaseException:
            if external:
                await cleanup(state)
            raise
        return state

    return solve


@scorer(metrics=[aggregate_speedup(), scored_attempts(), unscored_attempts()])
def inference_team_speedup(spec: dict[str, Any]) -> Scorer:
    """Retain the upstream final grade and separate team execution measurements."""
    module, name = spec["name"].rsplit(".", 1)
    upstream: Scorer = getattr(importlib.import_module(module), name)(**spec["args"])

    async def score(state: TaskState, target: Target) -> Score:
        """Grade only after peer join and preserve the result in authoritative typed state."""
        from datetime import datetime

        from collaboration_index.board.client import BoardHistory

        result = await upstream(state, target)
        if result is None:
            raise RuntimeError("The selected InferenceBench scorer returned no grade")
        history = state.store_as(TeamHistory)
        value = result.value
        if not isinstance(value, dict) or "speedup" not in value:
            raise RuntimeError("The selected InferenceBench scorer omitted speedup")
        state.store_as(InferenceHistory).final = {
            "value": value,
            "metadata": result.metadata,
        }
        history.result = {
            "speedup": value["speedup"],
            "tokens": sum(peer.tokens for peer in history.peers),
            "elapsed_seconds": (
                datetime.fromisoformat(history.completed or "")
                - datetime.fromisoformat(history.released or "")
            ).total_seconds(),
            "message_count": sum(
                call.completed and call.action == "send"
                for call in state.store_as(BoardHistory).calls
            ),
        }
        result.metadata = {**(result.metadata or {}), "team": history.result}
        return result

    return score


@task
def inferencebench(
    agents: int = ARGS["agents"],
    token_limit_per_agent: int | None = ARGS["token_limit_per_agent"],
    team_time_limit: float = ARGS["team_time_limit"],
    agent: str = ARGS["agent"],
    agent_args: dict[str, Any] = ARGS["agent_args"],
    artifact_dir: str = ARGS["artifact_dir"],
    compaction_threshold: float = ARGS["compaction_threshold"],
    context_window: int | None = ARGS["context_window"],
    grader_context_window: int | None = ARGS["grader_context_window"],
    gpu_config: str | None = ARGS["gpu_config"],
    gpu_management: str = ARGS["gpu_management"],
    scenarios: str | list[str] | None = ARGS["scenarios"],
    seed_pairs: list[list[int]] = ARGS["seed_pairs"],
    workload: dict[str, Any] = ARGS["workload"],
    tool_timeout: int = ARGS["tool_timeout"],
    web_search_args: dict[str, Any] = ARGS["web_search_args"],
    scorer: dict[str, Any] = ARGS["scorer"],
) -> Task:
    """Build team samples that optimize one inference server on one shared RunPod H100."""
    from collaboration_index.inferencebench.backend.dataset import get_inference_dataset

    if type(agents) is not int or not 1 <= agents <= 32:
        raise ValueError("InferenceBench team sizes must be between 1 and 32")
    if (
        team_time_limit is None
        or not math.isfinite(team_time_limit)
        or team_time_limit <= 0
    ):
        raise ValueError("Provide a positive team_time_limit in seconds")
    if type(tool_timeout) is not int or tool_timeout <= 0:
        raise ValueError("tool_timeout must be a positive integer")
    if gpu_management not in {"inspect", "controller"}:
        raise ValueError("gpu_management must be inspect or controller")
    if grader_context_window is not None and grader_context_window < 1:
        raise ValueError("grader_context_window must be a positive token count")
    validate_workload(workload)
    options = copy.deepcopy(workload)
    options.update(
        gpu_provider="runpod",
        gpu_config=str(Path(gpu_config).expanduser().resolve()) if gpu_config else None,
        context_length=context_window,
        agent_seconds=team_time_limit,
    )
    dataset = get_inference_dataset(scenarios, seed_pairs, options)
    for sample in dataset:
        sample.metadata = {
            **(sample.metadata or {}),
            "data": {},
            "sandbox_enabled": True,
            "benchmark_title": "InferenceBench",
            "team_size_disclosed": True,
            "gpu_management": gpu_management,
            "artifacts_root": str(Path(artifact_dir).expanduser().resolve()),
            "integrity_compaction_threshold": compaction_threshold
            if grader_context_window is None
            else int(grader_context_window * compaction_threshold),
        }
    return Task(
        dataset=dataset,
        setup=prepare_inference_team(
            agents,
            artifact_dir,
            tool_timeout,
            web_search_args,
            scorer["args"]["grader_role"],
            gpu_management == "controller",
        ),
        cleanup=cleanup,
        solver=team_agents(
            token_limit_per_agent,
            team_time_limit,
            agent,
            agent_args,
            compaction_threshold,
            context_window,
        ),
        scorer=inference_team_speedup(copy.deepcopy(scorer)),
        sandbox=SandboxEnvironmentSpec("inferencebench_runpod", options["gpu_config"])
        if gpu_management == "inspect"
        else None,
        config=GenerateConfig(**CONFIG["generate_config"]),
        epochs=Epochs(
            CONFIG["eval_config"]["epochs"], CONFIG["eval_config"]["epochs_reducer"]
        ),
        score_on_error=False,
        version=1,
        metadata={
            "team_sample": True,
            "condition": "collaborative",
            "agents": agents,
            "token_limit_per_agent": token_limit_per_agent,
            "planned_team_token_budget": None
            if token_limit_per_agent is None
            else agents * token_limit_per_agent,
            "context_window": context_window,
            "grader_context_window": grader_context_window,
            "source_commit": "8241a435ebe1cbb7fe5355f3b2ee3b7a85be884b",
            "gpu_management": gpu_management,
            "team_size_disclosed": True,
        },
    )
