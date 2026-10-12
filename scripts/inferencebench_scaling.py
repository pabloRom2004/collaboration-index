"""Plan InferenceBench team-size runs and collect their results.

``plan`` writes one Inspect run config per attempt and a manifest that lists
the attempts and their commands in a seeded random order. ``collect`` reads
finished ``.eval`` logs into one JSON line per attempt.

The design file (JSON or YAML) has these keys:

- ``study``: name prefix of every attempt.
- ``agents``: team sizes, for example ``[1, 2, 4, 8]``.
- ``budget_modes``: ``split`` and/or ``shared`` token budgets.
- ``gpu_policies``: ``fixed`` (one workspace GPU) and/or ``scaled`` (one per agent).
- ``team_tokens``: the team's total token budget.
- ``scenarios``, ``seed_pairs`` and ``repeats``: the blocks of the design.
- ``order_seed``: the seed of the execution order.
- ``artifact_root``: the directory below which each attempt keeps its artifacts.

A team size of 1 gets one cell per block, because budget modes and GPU
policies coincide for a single agent.
"""

import argparse
import copy
import hashlib
import json
import math
import random
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from inspect_ai.log import read_eval_log

from collaboration_index.inferencebench.task import MAX_WORKSPACE_GPUS, budget_plan

DEFAULT_CONFIG = Path("src/collaboration_index/inferencebench/run_configs/default.yaml")
# the model-request retry policy for every model run (AGENTS.md)
RETRY_POLICY = {"max_retries": 300, "attempt_timeout": 900}


def conditions(design: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand the design into distinct cells, with one cell for a single agent."""
    cells = []
    for agents in design["agents"]:
        if agents == 1:
            cells.append({"agents": 1, "budget_mode": "split", "gpu_policy": "fixed"})
            continue
        for mode in design["budget_modes"]:
            for policy in design["gpu_policies"]:
                cells.append(
                    {"agents": agents, "budget_mode": mode, "gpu_policy": policy}
                )
    return cells


def workspace_gpus(agents: int, policy: str) -> int:
    """Give one GPU for the fixed policy, or one GPU per agent up to the pod limit."""
    if policy == "fixed":
        return 1
    if policy == "scaled":
        return min(agents, MAX_WORKSPACE_GPUS)
    raise ValueError("gpu policy must be fixed or scaled")


def runs(design: dict[str, Any]) -> list[dict[str, Any]]:
    """List every attempt with its block and a seeded random execution order."""
    planned = []
    for scenario in design["scenarios"]:
        for pair in design["seed_pairs"]:
            for repeat in range(design["repeats"]):
                for cell in conditions(design):
                    name = (
                        f"{design['study']}-{scenario}-s{pair[0]}-{pair[1]}-r{repeat}-n{cell['agents']}"
                        f"-{cell['budget_mode']}-{cell['gpu_policy']}"
                    )
                    planned.append(
                        {
                            "run": name,
                            **cell,
                            "workspace_gpus": workspace_gpus(
                                cell["agents"], cell["gpu_policy"]
                            ),
                            "team_tokens": design["team_tokens"],
                            "scenario": scenario,
                            "seed_pair": list(pair),
                            "repeat": repeat,
                        }
                    )
    random.Random(design["order_seed"]).shuffle(planned)
    for index, item in enumerate(planned):
        item["order"] = index
    return planned


def run_config(
    base: dict[str, Any], item: dict[str, Any], artifact_root: str
) -> dict[str, Any]:
    """Overlay one attempt's condition on the packaged default run config."""
    config = copy.deepcopy(base)
    args = config["task"]["args"]
    args.update(
        agents=item["agents"],
        token_limit_per_agent=None,
        token_budget={"mode": item["budget_mode"], "tokens": item["team_tokens"]},
        gpu_management="controller",
        scenarios=item["scenario"],
        seed_pairs=[item["seed_pair"]],
        artifact_dir=f"{artifact_root}/{item['run']}",
    )
    args["orchestration"] = {
        **args["orchestration"],
        "enabled": True,
        "workspace_gpus": item["workspace_gpus"],
    }
    config["generate_config"].update(RETRY_POLICY)
    return config


def plan(design: dict[str, Any], base: dict[str, Any], out: Path) -> dict[str, Any]:
    """Write per-attempt configs and a manifest with the command for each attempt."""
    (out / "configs").mkdir(parents=True, exist_ok=False)
    items = runs(design)
    for item in items:
        text = yaml.safe_dump(
            run_config(base, item, design["artifact_root"]), sort_keys=False
        )
        path = out / "configs" / f"{item['run']}.yaml"
        path.write_text(text)
        item["config"] = str(path)
        item["config_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        item["command"] = (
            f"uv run inspect eval --run-config {path} "
            '--model "$CI_SUBJECT_MODEL" --model-role "integrity=$CI_INTEGRITY_MODEL" '
            f'-T context_window="$CI_CONTEXT_WINDOW" --metadata run={item["run"]} '
            f"--metadata repeat={item['repeat']} --log-dir logs"
        )
    manifest = {
        "created": datetime.now(UTC).isoformat(),
        "design": design,
        "attempts": len(items),
        "planned_team_tokens": design["team_tokens"] * len(items),
        "runs": items,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def row(
    task_args: dict[str, Any],
    metadata: dict[str, Any],
    store: dict[str, Any],
    score: dict[str, Any] | None,
    error: bool,
) -> dict[str, Any]:
    """Flatten one attempt into a row that marks unscored and failed attempts."""
    budget = task_args.get("token_budget")
    orchestration = task_args.get("orchestration") or {}
    seeds = (task_args.get("seed_pairs") or [[None, None]])[0]
    result = store.get("TeamHistory:result") or {}
    measured = store.get("InferenceHistory:orchestration") or {}
    peers = store.get("TeamHistory:peers") or []
    speedup = None if score is None else score.get("speedup")
    if error or speedup is None:
        status = "error"
    elif not math.isfinite(float(speedup)):
        status = "unscored"
    else:
        status = "scored"
    gpu = measured.get("gpu") or {}
    leases = measured.get("leases") or {}
    submissions = measured.get("submissions") or {}
    checks = store.get("InferenceHistory:checks") or []
    agents = task_args.get("agents")
    tokens = sum(p.get("tokens", 0) for p in peers)
    planned = (
        budget_plan(budget["mode"], budget["tokens"], agents)["planned_team_total"]
        if budget and agents
        else None
    )
    return {
        "run": metadata.get("run"),
        "agents": agents,
        "budget_mode": "solo" if agents == 1 else (budget or {}).get("mode"),
        "team_tokens": (budget or {}).get("tokens"),
        "workspace_gpus": orchestration.get("workspace_gpus"),
        "scenario": task_args.get("scenarios"),
        "dev_seed": seeds[0],
        "eval_seed": seeds[1],
        "repeat": metadata.get("repeat"),
        "status": status,
        "speedup": float(speedup) if status == "scored" else None,
        "submitted": result.get("submitted"),
        "submissions": submissions.get("count"),
        "valid_submissions": submissions.get("valid"),
        "first_submission_seconds": submissions.get("first_submission_seconds"),
        "first_valid_seconds": submissions.get("first_valid_seconds"),
        "best_in_run_speedup": submissions.get("best_in_run_speedup"),
        "final_in_run_speedup": (submissions.get("final_in_run_result") or {}).get(
            "speedup"
        ),
        "solve_seconds": result.get("elapsed_seconds"),
        "tokens": tokens,
        "agent_tokens": [p.get("tokens", 0) for p in peers],
        "overshoot_tokens": max(0, tokens - planned) if planned else None,
        "proxy_speedups": [
            c["proxy_speedup"] for c in checks if c.get("proxy_speedup") is not None
        ],
        "contended_checks": sum(bool(c.get("contended")) for c in checks),
        "revoked_leases": (leases.get("end_reasons") or {}).get("expired", 0),
        "extra_gpus_reclaimed": leases.get("extra_gpus_reclaimed"),
        "max_gpus_per_lease": leases.get("max_gpus_per_lease"),
        "lease_wait_p50_seconds": (leases.get("wait_seconds") or {}).get("p50"),
        "gpu_occupancy": leases.get("occupancy"),
        "messages": result.get("message_count"),
        "observed_gpu_hours": gpu.get("observed_gpu_hours"),
        "busy_gpu_hours": gpu.get("busy_gpu_hours"),
        "energy_watt_hours": gpu.get("energy_watt_hours"),
        "oom_events": measured.get("oom_events"),
        "unleased_gpu_observations": gpu.get("unleased_gpu_process_observations"),
    }


def collect(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Read finished Inspect logs into rows."""
    rows = []
    for path in paths:
        log = read_eval_log(str(path))
        metadata = dict(log.eval.metadata or {})
        if metadata.get("repeat") is not None:
            # command-line metadata arrives as text
            metadata["repeat"] = int(metadata["repeat"])
        for sample in log.samples or []:
            score = (
                sample.scores.get("inference_team_speedup") if sample.scores else None
            )
            rows.append(
                row(
                    log.eval.task_args or {},
                    metadata,
                    sample.store,
                    score.value if score is not None else None,
                    sample.error is not None,
                )
            )
    return rows


def main() -> None:
    """Dispatch plan or collect from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    planner = commands.add_parser("plan", help="write run configs and a manifest")
    planner.add_argument("design", type=Path, help="study design JSON or YAML")
    planner.add_argument(
        "--out", type=Path, required=True, help="new directory under run-artifacts/"
    )
    planner.add_argument("--base", type=Path, default=DEFAULT_CONFIG)
    collector = commands.add_parser("collect", help="read .eval logs into JSON lines")
    collector.add_argument("logs", nargs="+", type=Path)
    collector.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        manifest = plan(
            yaml.safe_load(args.design.read_text()),
            yaml.safe_load(args.base.read_text()),
            args.out,
        )
        print(f"Wrote {manifest['attempts']} configs and {args.out / 'manifest.json'}")
    else:
        rows = collect(args.logs)
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
