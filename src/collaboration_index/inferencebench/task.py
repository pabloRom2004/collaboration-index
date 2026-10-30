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
from collaboration_index.inferencebench.orchestration import SCORING_RULES
from collaboration_index.inferencebench.resource import (
    WORKSPACE_KEY,
    allocate,
    terminate,
)
from collaboration_index.inferencebench.session import (
    Session,
    cleanup,
    current,
    open_session,
)
from collaboration_index.inferencebench.state import InferenceHistory
from collaboration_index.inferencebench.tools import evaluate, workspace_tools
from collaboration_index.inferencebench import team_workspace as team_mode
from collaboration_index.state import TeamHistory
from collaboration_index.task import defaults

CONFIG = defaults("inferencebench")
ARGS = CONFIG["task"]["args"]
MAX_AGENTS = 64
MAX_WORKSPACE_GPUS = 8
BUDGET_MODES = ("split", "shared")
GPU_ACCESS = ("scheduler", "unmanaged")


def budget_plan(mode: str, tokens: int, agents: int) -> dict[str, Any]:
    """Translate a team token budget into a per-agent cap or a shared pool."""
    if mode not in BUDGET_MODES:
        raise ValueError(f"token_budget mode must be one of {', '.join(BUDGET_MODES)}")
    if type(tokens) is not int or tokens < agents:
        raise ValueError(
            "token_budget tokens must be an integer of at least one per agent"
        )
    split = mode == "split"
    # split gives every agent the same floor share; the remainder (< agents) is not allocated
    return {
        "mode": mode,
        "tokens": tokens,
        "per_agent_cap": tokens // agents if split else None,
        "team_pool": None if split else tokens,
        "planned_team_total": tokens // agents * agents if split else tokens,
        "unallocated_tokens": tokens % agents if split else 0,
    }


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


def validate_orchestration(options: dict[str, Any], workload: dict[str, Any]) -> None:
    """Reject malformed leased-GPU settings before allocating a paid GPU."""
    if set(options) != set(ARGS["orchestration"]) or set(options["dev_eval"]) != set(
        ARGS["orchestration"]["dev_eval"]
    ):
        raise ValueError("orchestration must contain exactly the documented fields")
    if type(options["enabled"]) is not bool:
        raise ValueError("orchestration enabled must be a boolean")
    counts = [
        "max_parallel_commands",
        "max_pending_submissions",
        "max_submission_bytes",
    ]
    if any(type(options[k]) is not int or options[k] < 1 for k in counts):
        raise ValueError("orchestration counts and sizes must be positive integers")
    gpus = options["workspace_gpus"]
    if type(gpus) is not int or not 1 <= gpus <= MAX_WORKSPACE_GPUS:
        raise ValueError(
            f"workspace_gpus must be an integer from 1 to {MAX_WORKSPACE_GPUS}"
        )
    for name in ["lease_seconds", "monitor_seconds"]:
        value = options[name]
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
    if options["enforce"] not in {"audit", "kill"}:
        raise ValueError("enforce must be audit or kill")
    if options["gpu_access"] not in GPU_ACCESS:
        raise ValueError(f"gpu_access must be one of {', '.join(GPU_ACCESS)}")
    if options["gpu_access"] == "unmanaged" and options["enforce"] == "kill":
        raise ValueError("enforce: kill needs gpu_access: scheduler")
    dev = options["dev_eval"]
    counts = [
        "requests",
        "quick_requests",
        "quality_samples",
        "quick_quality_samples",
        "server_wait_seconds",
    ]
    if any(type(dev[k]) is not int or dev[k] < 1 for k in counts):
        raise ValueError("dev_eval counts and durations must be positive integers")
    if type(dev["quality_seed"]) is not int or dev["quality_seed"] < 0:
        raise ValueError("dev_eval quality_seed must be a nonnegative integer")
    if (
        dev["quick_requests"] > dev["requests"]
        or dev["quick_quality_samples"] > dev["quality_samples"]
    ):
        raise ValueError(
            "quick development inputs must not exceed the full development inputs"
        )
    if options["enabled"] and dev["quality_seed"] == workload["quality_seed"]:
        raise ValueError(
            "The development quality seed must differ from the held-out quality seed"
        )


def validate_scoring(scoring: str, evaluation: dict[str, Any]) -> None:
    """Reject an unknown scoring rule or malformed submission-recording flags."""
    if scoring not in SCORING_RULES:
        raise ValueError(f"scoring must be one of {', '.join(SCORING_RULES)}")
    if (
        not isinstance(evaluation, dict)
        or set(evaluation) != set(ARGS["evaluation"])
        or any(type(value) is not bool for value in evaluation.values())
    ):
        raise ValueError("evaluation must contain exactly the documented boolean flags")


@solver
def prepare_inference_team(
    agents: int,
    artifact_dir: str,
    tool_timeout: int,
    web_search_args: dict[str, Any],
    grader_role: str,
    external: bool,
    orchestration: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    team_workspace: dict[str, Any] | None = None,
) -> Solver:
    """Require an explicit integrity judge and prepare the GPUs before releasing any peers."""
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
        if external and orchestration is None:
            await allocate(state)
        try:
            if orchestration is None:
                state = await upstream(state, generate)
            else:
                # a grading pod holds the held-out inputs; a workspace pod serves the peers
                state = await open_session(
                    state, orchestration, budget, tool_timeout, upstream, generate
                )
            state = await team(state, generate)
            state.tools = [
                *workspace_tools(tool_timeout, external),
                evaluate(tool_timeout),
                web_search(**web_search_args),
            ]
            if team_workspace is not None:
                await team_mode.setup(
                    [peer.id for peer in state.store_as(TeamHistory).peers],
                    {**team_workspace, "tool_timeout": tool_timeout},
                )
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

    async def final_submission(
        state: TaskState, target: Target, session: Session
    ) -> Score | None:
        """Grade the submission the scoring rule selects on the restarted grader, or apply the 1.0 fallback."""
        # the monitor covers solving time only; queued submissions are still graded
        await session.stop("monitor")
        await session.queue.drain()
        await session.stop()
        # final scoring uses only the grading pod, so stop paying for the idle workspace
        try:
            await terminate(state, WORKSPACE_KEY, "workspace_gpu_lifecycle")
        except Exception as error:
            # the pod stays registered, so cleanup retries the delete after scoring
            session.event("workspace_terminate_error", error=type(error).__name__)
        selected = session.queue.select()
        common = {
            "scoring": session.queue.scoring,
            "submitted": bool(session.queue.items),
            "submissions": len(session.queue.items),
        }
        if selected is None:
            if state.store_as(TeamHistory).end_reason == "workspace_unreachable":
                # a lost pod is an infrastructure failure, not a 1.0 team result
                raise RuntimeError("The workspace pod became unreachable before any valid submission")
            result: Score | None = Score(
                value={"speedup": 1.0},
                explanation="The team made no valid submission; the upstream baseline fallback applies.",
                metadata={**common, "final_submission": None},
            )
        else:
            # the selected snapshot is measured again, now with the integrity judge
            await session.install(selected)
            result = await upstream(state, target)
            if result is not None:
                result.metadata = {
                    **(result.metadata or {}),
                    **common,
                    "final_submission": selected.number,
                    "final_digest": selected.digest,
                    "final_archive": selected.archive,
                    "final_in_run_result": session.queue.origin(selected).result,
                }
        # a missing grade fails scoring before any archive is pruned
        if result is not None:
            session.finalize(selected, result.value)
        return result

    async def score(state: TaskState, target: Target) -> Score:
        """Grade only after peer join and preserve the result in authoritative typed state."""
        from datetime import datetime

        from collaboration_index.board.client import BoardHistory

        session = current()
        team_state = team_mode.active()
        if session is not None:
            result = await final_submission(state, target, session)
        elif team_state is not None and not team_state.submissions:
            result = Score(
                value={"speedup": 1.0},
                explanation="The team made no submission; the upstream baseline fallback applies.",
                metadata={"submitted": False},
            )
        else:
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
        team = {
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
        if session is not None:
            measured = state.store_as(InferenceHistory).orchestration
            submissions, gpu = measured["submissions"], measured["gpu"]
            team.update(
                scoring=submissions["scoring"],
                final_submission=submissions["final_submission"],
                submitted=bool(submissions["count"]),
                submissions=submissions["count"],
                valid_submissions=submissions["valid"],
                first_valid_seconds=submissions["first_valid_seconds"],
                best_in_run_speedup=submissions["best_in_run_speedup"],
                lease_wait_p50_seconds=measured["leases"]["wait_seconds"]["p50"],
                gpu_occupancy=measured["leases"]["occupancy"],
                busy_gpu_hours=gpu["busy_gpu_hours"],
                oom_events=measured["oom_events"],
            )
        if team_state is not None:
            team.update(
                submissions=len(team_state.submissions),
                final_submission=(team_state.submissions or [{}])[-1].get("number"),
                # the code archives stay only in the TeamWorkspace store
                submission_log=[{k: v for k, v in s.items() if k != "code"} for s in team_state.submissions],
                tracked_submissions=team_state.tracked,
            )
        # assign the store field once; mutating a coerced read can be lost
        history.result = team
        result.metadata = {**(result.metadata or {}), "team": team}
        return result

    return score


@task
def inferencebench(
    agents: int = ARGS["agents"],
    token_limit_per_agent: int | None = ARGS["token_limit_per_agent"],
    team_time_limit: float | None = ARGS["team_time_limit"],
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
    token_budget: dict[str, Any] | None = ARGS["token_budget"],
    orchestration: dict[str, Any] = ARGS["orchestration"],
    team_workspace: dict[str, Any] = ARGS["team_workspace"],
    scoring: str = ARGS["scoring"],
    evaluation: dict[str, bool] = ARGS["evaluation"],
) -> Task:
    """Build team samples that optimize one inference server on shared RunPod H100s."""
    from collaboration_index.inferencebench.backend.dataset import get_inference_dataset

    if type(agents) is not int or not 1 <= agents <= MAX_AGENTS:
        raise ValueError(
            f"InferenceBench team sizes must be between 1 and {MAX_AGENTS}"
        )
    validate_orchestration(orchestration, workload)
    team_mode.validate(team_workspace)
    validate_scoring(scoring, evaluation)
    leased = orchestration["enabled"]
    if leased and gpu_management != "controller":
        raise ValueError("orchestration needs gpu_management: controller")
    plan = None
    if token_budget is None:
        planned = (
            None if token_limit_per_agent is None else agents * token_limit_per_agent
        )
    else:
        if not leased:
            raise ValueError("token_budget needs orchestration enabled")
        if set(token_budget) != {"mode", "tokens"} or token_limit_per_agent is not None:
            raise ValueError(
                "token_budget needs exactly mode and tokens, without token_limit_per_agent"
            )
        plan = budget_plan(token_budget["mode"], token_budget["tokens"], agents)
        token_limit_per_agent = plan["per_agent_cap"]
        planned = plan["planned_team_total"]
    if team_time_limit is not None and (
        not math.isfinite(team_time_limit) or team_time_limit <= 0
    ):
        raise ValueError("Provide positive team_time_limit seconds or null")
    if type(tool_timeout) is not int or tool_timeout <= 0:
        raise ValueError("tool_timeout must be a positive integer")
    if gpu_management not in {"inspect", "controller"}:
        raise ValueError("gpu_management must be inspect or controller")
    if grader_context_window is not None and grader_context_window < 1:
        raise ValueError("grader_context_window must be a positive token count")
    validate_workload(workload)
    teamed = team_workspace["enabled"]
    if teamed and (leased or gpu_management != "controller" or gpu_config):
        raise ValueError(
            "team_workspace needs orchestration disabled, gpu_management: controller "
            "and no gpu_config (it writes its own pod config)"
        )
    if teamed:
        gpu_config = team_mode.pod_config(team_workspace, artifact_dir)
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
            {**orchestration, "scoring": scoring, "evaluation": dict(evaluation)}
            if leased
            else None,
            plan,
            dict(team_workspace) if teamed else None,
        ),
        cleanup=cleanup,
        solver=team_agents(
            token_limit_per_agent,
            team_time_limit,
            agent,
            agent_args,
            compaction_threshold,
            context_window,
            team_token_pool=None if plan is None else plan["team_pool"],
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
            "planned_team_token_budget": planned,
            "token_budget": plan,
            "context_window": context_window,
            "grader_context_window": grader_context_window,
            "source_commit": "8241a435ebe1cbb7fe5355f3b2ee3b7a85be884b",
            "gpu_management": gpu_management,
            "team_size_disclosed": True,
            "orchestration": orchestration,
            "team_workspace": team_workspace,
            "scoring": scoring,
            "evaluation": evaluation,
        },
    )
