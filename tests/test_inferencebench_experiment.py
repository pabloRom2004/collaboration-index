"""Check that the InferenceBench experiment file resolves into the intended runs and task arguments."""

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("asyncssh")

SCRIPT = Path(__file__).parents[1] / "scripts" / "inferencebench_experiment.py"
spec = importlib.util.spec_from_file_location("inferencebench_experiment", SCRIPT)
assert spec and spec.loader
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

DEFAULTS: dict[str, Any] = yaml.safe_load(script.DEFAULT_EXPERIMENT.read_text())


def resolve(**changes: Any) -> dict[str, Any]:
    """Plan the packaged experiment file with some settings changed."""
    return script.plan({**copy.deepcopy(DEFAULTS), **changes}, "/tmp/experiment")


def test_packaged_defaults_resolve_to_four_concurrent_timed_runs() -> None:
    """Run the four scenarios at once, epochs in sequence, on 4 + 1 H100s each, for 8 hours."""
    assert DEFAULTS["num_agents"] == 1 and DEFAULTS["epochs"] == 3
    plan = resolve()
    assert plan["runs"] == 12 and plan["concurrent_runs"] == 4
    assert plan["peak_gpus"] == 20 and plan["peak_agents"] == 4
    assert plan["gpu_type"] == "NVIDIA H100 80GB HBM3"
    assert plan["eval"]["max_tasks"] == 4 and plan["eval"]["max_samples"] == 1
    assert plan["eval"]["model"] == "anthropic/claude-haiku-5-5"
    assert plan["eval"]["max_retries"] == 300
    assert plan["seconds_per_run"] == 8 * 3600 and plan["token_budget_per_run"] is None
    args = plan["tasks"]["B"]
    assert args["scenarios"] == "B" and args["team_time_limit"] == 8 * 3600
    assert args["token_budget"] is None and args["token_limit_per_agent"] is None
    assert args["workload"]["base_model"] == "Qwen/Qwen3.5-35B-A3B"
    assert args["orchestration"]["enabled"] is True
    assert args["orchestration"]["workspace_gpus"] == 4
    assert args["orchestration"]["gpu_access"] == "unmanaged"
    assert args["scoring"] == "best_submit"
    assert args["gpu_config"] == "/tmp/experiment/runpod-provider.yaml"
    assert args["artifact_dir"] == "/tmp/experiment/B"


def test_parallel_epochs_get_their_own_gpus_and_agents() -> None:
    """Start every epoch at once, each with its own GPUs and agents, as in 4 GPUs x 16 agents x 3 epochs."""
    plan = resolve(num_agents=16, scenarios=["A"], epoch_sequential=False)
    assert plan["concurrent_runs"] == 3 and plan["eval"]["max_samples"] == 3
    assert plan["peak_agents"] == 48 and plan["peak_gpus"] == 3 * (4 + 1)
    assert plan["eval"]["max_connections"] == 48


@pytest.mark.parametrize(
    ("budget", "mode", "cap", "pool"),
    [
        ("token_shared", "shared", None, 4_000_000),
        ("token_uniform", "split", 62_500, None),
    ],
)
def test_token_budgets_replace_the_time_limit(
    budget: str, mode: str, cap: int | None, pool: int | None
) -> None:
    """Map token budgets to the task's shared pool or equal split, with no wall-clock limit."""
    plan = resolve(budget=budget, num_agents=64)
    args = plan["tasks"]["A"]
    assert args["team_time_limit"] is None and plan["seconds_per_run"] is None
    assert args["token_budget"] == {"mode": mode, "tokens": 4_000_000}
    budget_plan = plan["token_budget_per_run"]
    assert budget_plan["per_agent_cap"] == cap and budget_plan["team_pool"] == pool
    assert budget_plan["unallocated_tokens"] == 0


def test_tasks_accept_every_resolved_argument() -> None:
    """Build the real task from each resolved argument set without allocating anything."""
    from collaboration_index.inferencebench import inferencebench

    for budget in ("time", "token_shared", "token_uniform"):
        for access in ("unmanaged", "scheduler"):
            plan = resolve(budget=budget, gpu_access_mode=access, num_agents=64)
            task = inferencebench(**plan["tasks"]["A"])
            assert task.metadata["agents"] == 64
            assert task.metadata["orchestration"]["gpu_access"] == access


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"num_agents": 65}, "num_agents"),
        ({"num_gpus": 9}, "num_gpus"),
        ({"budget": "gpu_time"}, "budget"),
        ({"budget": "token_uniform", "num_agents": 16, "token_budget": 8}, "token"),
        ({"wall_clock_hours": 0}, "wall_clock_hours"),
        ({"wall_clock_hours": float("inf")}, "wall_clock_hours"),
        ({"evaluation": [True]}, "evaluation"),
        ({"epochs": 0}, "epochs"),
        ({"epoch_sequential": "yes"}, "epoch_sequential"),
        ({"scoring": "first_submit"}, "scoring"),
        ({"scenarios": ["A", "E"]}, "scenarios"),
        ({"scenarios": ["A", "A"]}, "scenarios"),
        ({"gpu_access_mode": "locked"}, "gpu_access_mode"),
        ({"evaluation": {"log_all_submissions": True}}, "evaluation"),
        ({"agent_model": ""}, "agent_model"),
        ({"typo": 1}, "unknown keys"),
    ],
)
def test_invalid_settings_are_rejected(changes: dict[str, Any], message: str) -> None:
    """Refuse a setting the experiment does not define instead of silently ignoring it."""
    with pytest.raises(ValueError, match=message):
        resolve(**changes)


def test_run_starts_one_named_task_per_scenario_with_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass the planned concurrency, epochs and model to one eval call and write the plan files."""
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        script, "inspect_eval", lambda tasks, **kwargs: calls.append((tasks, kwargs))
    )
    config = {**copy.deepcopy(DEFAULTS), "epoch_sequential": False}
    root = tmp_path / "experiment"
    plan = script.plan(config, str(root))
    script.run(config, plan, "mockllm/judge", str(tmp_path / "logs"), None, None)
    [(tasks, kwargs)] = calls
    assert [t.name for t in tasks] == [f"inferencebench_{s}" for s in "ABCD"]
    assert kwargs["max_tasks"] == 4 and kwargs["max_samples"] == 3
    assert kwargs["epochs"].epochs == 3
    assert kwargs["model"] == "anthropic/claude-haiku-5-5"
    assert kwargs["model_roles"] == {"integrity": "mockllm/judge"}
    assert kwargs["max_connections"] == 12 and kwargs["max_retries"] == 300
    assert kwargs["metadata"]["experiment"] == config
    provider = yaml.safe_load((root / "runpod-provider.yaml").read_text())
    assert provider["pod"]["gpuTypeIds"] == ["NVIDIA H100 80GB HBM3"]
    assert (root / "plan.json").is_file() and (root / "experiment.json").is_file()


def test_provider_config_requests_the_gpu_type(tmp_path: Path) -> None:
    """Keep the bundled RunPod settings and replace only the GPU type."""
    path = tmp_path / "runpod.yaml"
    script.write_provider("NVIDIA H200", path)
    provider = yaml.safe_load(path.read_text())
    assert provider["pod"]["gpuTypeIds"] == ["NVIDIA H200"]
    assert provider["pod"]["gpuCount"] == 1 and provider["bootstrap"] is True
