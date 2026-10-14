"""Plan or run the InferenceBench team-size experiment described by one YAML file.

``plan`` prints the resolved runs, concurrency and GPU totals without starting
anything. ``run`` writes the provider config and the plan below
``--artifact-root`` and starts one Inspect eval with one task per scenario.
Scenarios run at the same time (``max_tasks``); ``epoch_sequential`` sets how
many epochs of one scenario run at once (``max_samples``). Every scenario x
epoch is a separate run with its own pods, agents and board.

The file's keys are documented in ``scripts/inferencebench_experiment.yaml``.
A run needs an explicit integrity judge model (``--integrity-model``) and the
agent model's credentials; this script selects neither credential.
"""

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Epochs, task_with
from inspect_ai import eval as inspect_eval

from collaboration_index.inferencebench import inferencebench
from collaboration_index.inferencebench.backend.dataset import SCENARIOS
from collaboration_index.inferencebench.backend.run_config import load_config
from collaboration_index.inferencebench.task import (
    ARGS,
    CONFIG,
    GPU_ACCESS,
    MAX_AGENTS,
    MAX_WORKSPACE_GPUS,
    budget_plan,
    validate_scoring,
)

DEFAULT_EXPERIMENT = Path(__file__).with_name("inferencebench_experiment.yaml")
KEYS = {
    "num_agents",
    "num_gpus",
    "gpu_type",
    "base_model",
    "budget",
    "wall_clock_hours",
    "token_budget",
    "epochs",
    "epoch_sequential",
    "scoring",
    "scenarios",
    "agent_model",
    "gpu_access_mode",
    "evaluation",
}
# the experiment's budget names and the task's token_budget modes
TOKEN_MODES = {"token_shared": "shared", "token_uniform": "split"}
BUDGETS = ("time", *TOKEN_MODES)
# the model-request retry policy for every model run (AGENTS.md)
RETRY_POLICY = {"max_retries": 300, "attempt_timeout": 900}


def validate(config: dict[str, Any]) -> None:
    """Reject missing, unknown or out-of-range experiment settings before anything starts."""
    if set(config) != KEYS:
        missing, unknown = KEYS - set(config), set(config) - KEYS
        raise ValueError(
            f"missing keys {sorted(missing)}, unknown keys {sorted(unknown)}"
        )
    agents, gpus = config["num_agents"], config["num_gpus"]
    if type(agents) is not int or not 1 <= agents <= MAX_AGENTS:
        raise ValueError(f"num_agents must be an integer from 1 to {MAX_AGENTS}")
    if type(gpus) is not int or not 1 <= gpus <= MAX_WORKSPACE_GPUS:
        raise ValueError(f"num_gpus must be an integer from 1 to {MAX_WORKSPACE_GPUS}")
    for key in ("gpu_type", "base_model", "agent_model"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if config["budget"] not in BUDGETS:
        raise ValueError(f"budget must be one of {', '.join(BUDGETS)}")
    hours = config["wall_clock_hours"]
    if type(hours) not in {int, float} or not math.isfinite(hours) or hours <= 0:
        raise ValueError("wall_clock_hours must be a finite positive number")
    if config["budget"] in TOKEN_MODES:
        budget_plan(TOKEN_MODES[config["budget"]], config["token_budget"], agents)
    if type(config["epochs"]) is not int or config["epochs"] < 1:
        raise ValueError("epochs must be a positive integer")
    if type(config["epoch_sequential"]) is not bool:
        raise ValueError("epoch_sequential must be true or false")
    scenarios = config["scenarios"]
    if (
        not isinstance(scenarios, list)
        or not scenarios
        or len(set(scenarios)) != len(scenarios)
        or set(scenarios) - SCENARIOS.keys()
    ):
        raise ValueError(f"scenarios must be distinct IDs from {sorted(SCENARIOS)}")
    if config["gpu_access_mode"] not in GPU_ACCESS:
        raise ValueError(f"gpu_access_mode must be one of {', '.join(GPU_ACCESS)}")
    validate_scoring(config["scoring"], config["evaluation"])


def task_args(
    config: dict[str, Any], scenario: str, artifact_root: str, gpu_config: str
) -> dict[str, Any]:
    """Translate the experiment settings into InferenceBench task arguments for one scenario."""
    timed = config["budget"] == "time"
    return {
        "agents": config["num_agents"],
        "token_limit_per_agent": None,
        "team_time_limit": config["wall_clock_hours"] * 3600 if timed else None,
        "token_budget": None
        if timed
        else {"mode": TOKEN_MODES[config["budget"]], "tokens": config["token_budget"]},
        "gpu_management": "controller",
        "gpu_config": gpu_config,
        "scenarios": scenario,
        "artifact_dir": f"{artifact_root}/{scenario}",
        "workload": {**ARGS["workload"], "base_model": config["base_model"]},
        "orchestration": {
            **ARGS["orchestration"],
            "enabled": True,
            "workspace_gpus": config["num_gpus"],
            "gpu_access": config["gpu_access_mode"],
        },
        "scoring": config["scoring"],
        "evaluation": dict(config["evaluation"]),
    }


def plan(config: dict[str, Any], artifact_root: str) -> dict[str, Any]:
    """Resolve the experiment into per-scenario task arguments, concurrency and resource totals."""
    validate(config)
    scenarios, epochs = config["scenarios"], config["epochs"]
    per_scenario = 1 if config["epoch_sequential"] else epochs
    concurrent = len(scenarios) * per_scenario
    gpu_config = f"{artifact_root}/runpod-provider.yaml"
    budget = (
        None
        if config["budget"] == "time"
        else budget_plan(
            TOKEN_MODES[config["budget"]], config["token_budget"], config["num_agents"]
        )
    )
    return {
        "artifact_root": artifact_root,
        "runs": len(scenarios) * epochs,
        "concurrent_runs": concurrent,
        "gpus_per_run": {"development": config["num_gpus"], "grading": 1},
        "peak_gpus": concurrent * (config["num_gpus"] + 1),
        "gpu_type": config["gpu_type"],
        "peak_agents": concurrent * config["num_agents"],
        "seconds_per_run": config["wall_clock_hours"] * 3600
        if config["budget"] == "time"
        else None,
        "token_budget_per_run": budget,
        "eval": {
            "model": config["agent_model"],
            "epochs": epochs,
            "epochs_reducer": CONFIG["eval_config"]["epochs_reducer"],
            "max_tasks": len(scenarios),
            "max_samples": per_scenario,
            # one model connection per concurrent agent, so the API pool is not the bottleneck
            "max_connections": concurrent * config["num_agents"],
            **RETRY_POLICY,
        },
        "tasks": {
            scenario: task_args(config, scenario, artifact_root, gpu_config)
            for scenario in scenarios
        },
    }


def write_provider(gpu_type: str, path: Path) -> None:
    """Write the bundled RunPod provider config with the requested GPU type for both pods."""
    provider = load_config("runpod.yaml")
    provider["pod"]["gpuTypeIds"] = [gpu_type]
    path.write_text(yaml.safe_dump(provider, sort_keys=False))


def run(
    config: dict[str, Any],
    resolved: dict[str, Any],
    integrity_model: str,
    log_dir: str,
    context_window: int | None,
    grader_context_window: int | None,
) -> None:
    """Write the provider config and plan, then run every scenario as its own concurrent task."""
    # building the tasks first rejects bad arguments before anything is written
    options = resolved["eval"]
    tasks = [
        task_with(
            inferencebench(
                **args,
                context_window=context_window,
                grader_context_window=grader_context_window,
            ),
            name=f"inferencebench_{scenario}",
        )
        for scenario, args in resolved["tasks"].items()
    ]
    root = Path(resolved["artifact_root"])
    root.mkdir(parents=True, exist_ok=False)
    write_provider(config["gpu_type"], root / "runpod-provider.yaml")
    text = json.dumps(config, sort_keys=True)
    (root / "experiment.json").write_text(json.dumps(config, indent=2))
    (root / "plan.json").write_text(json.dumps(resolved, indent=2))
    inspect_eval(
        tasks,
        model=options["model"],
        model_roles={"integrity": integrity_model},
        epochs=Epochs(options["epochs"], options["epochs_reducer"]),
        max_tasks=options["max_tasks"],
        max_samples=options["max_samples"],
        max_connections=options["max_connections"],
        max_retries=options["max_retries"],
        attempt_timeout=options["attempt_timeout"],
        log_dir=log_dir,
        metadata={
            "experiment": config,
            "experiment_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "artifact_root": str(root),
        },
    )


def main() -> None:
    """Parse the command line and print the plan or start the experiment."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for name in ("plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, default=DEFAULT_EXPERIMENT)
        sub.add_argument(
            "--artifact-root",
            default=f"run-artifacts/inferencebench-experiment-{stamp}",
            help="new directory for provider config, plan and run artifacts",
        )
    runner = commands.choices["run"]
    runner.add_argument("--integrity-model", required=True)
    runner.add_argument("--log-dir", default="logs")
    runner.add_argument("--context-window", type=int)
    runner.add_argument("--grader-context-window", type=int)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    root = str(Path(args.artifact_root).resolve())
    resolved = plan(config, root)
    if args.command == "plan":
        print(json.dumps(resolved, indent=2))
        return
    run(
        config,
        resolved,
        args.integrity_model,
        args.log_dir,
        args.context_window,
        args.grader_context_window,
    )


if __name__ == "__main__":
    main()
