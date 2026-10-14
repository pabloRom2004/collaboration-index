"""Check the InferenceBench team-size planner and log collector."""

import hashlib
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "inferencebench_scaling.py"
spec = importlib.util.spec_from_file_location("inferencebench_scaling", SCRIPT)
assert spec and spec.loader
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

DESIGN = {
    "study": "demo",
    "agents": [1, 2, 4, 8],
    "budget_modes": ["split", "shared"],
    "gpu_policies": ["fixed", "scaled"],
    "team_tokens": 8_000_000,
    "scenarios": ["A"],
    "seed_pairs": [[21, 1337]],
    "repeats": 2,
    "order_seed": 7,
    "artifact_root": "run-artifacts/demo",
}
BASE = {
    "task": {
        "args": {
            "agents": 2,
            "token_limit_per_agent": None,
            "token_budget": None,
            "gpu_management": "inspect",
            "scenarios": "A",
            "seed_pairs": [[21, 1337]],
            "artifact_dir": "run-artifacts/inferencebench-team",
            "orchestration": {"enabled": False, "workspace_gpus": 1},
        }
    },
    "generate_config": {"max_retries": 10},
}


def test_runs_keep_one_solo_cell_per_block_in_a_seeded_order() -> None:
    """Plan one solo attempt and every team cell per block, in a reproducible order."""
    runs = script.runs(DESIGN)
    assert len(runs) == 2 * (1 + 3 * 2 * 2)
    assert len({r["run"] for r in runs}) == len(runs)
    assert sum(r["agents"] == 1 for r in runs) == 2
    scaled = {r["workspace_gpus"] for r in runs if r["gpu_policy"] == "scaled"}
    assert scaled == {2, 4, 8}
    assert [r["run"] for r in runs] == [r["run"] for r in script.runs(DESIGN)]
    assert [r["order"] for r in runs] == list(range(len(runs)))
    with pytest.raises(ValueError):
        script.workspace_gpus(2, "elastic")


def test_plan_writes_configs_and_a_manifest(tmp_path: Path) -> None:
    """Enable orchestration with each cell's budget and GPUs, hash the config and keep the base."""
    manifest = script.plan(DESIGN, BASE, tmp_path / "study")
    assert manifest["attempts"] == 26
    assert BASE["task"]["args"]["orchestration"]["enabled"] is False
    item = next(
        r for r in manifest["runs"] if r["agents"] == 8 and r["gpu_policy"] == "scaled"
    )
    text = Path(item["config"]).read_text()
    args = yaml.safe_load(text)["task"]["args"]
    assert args["token_budget"] == {"mode": item["budget_mode"], "tokens": 8_000_000}
    assert args["orchestration"] == {"enabled": True, "workspace_gpus": 8}
    assert args["gpu_management"] == "controller"
    assert yaml.safe_load(text)["generate_config"]["max_retries"] == 300
    assert f"--metadata run={item['run']}" in item["command"]
    assert hashlib.sha256(text.encode()).hexdigest() == item["config_sha256"]
    with pytest.raises(FileExistsError):
        script.plan(DESIGN, BASE, tmp_path / "study")


def store(submissions: int = 2) -> dict[str, Any]:
    """Build an authored sample store with leased-GPU measurements."""
    return {
        "TeamHistory:result": {
            "submitted": True,
            "elapsed_seconds": 100.0,
            "message_count": 3,
        },
        "TeamHistory:peers": [{"tokens": 10}, {"tokens": 20}],
        "InferenceHistory:checks": [
            {"proxy_speedup": 1.5, "contended": True},
            {"proxy_speedup": None, "contended": False},
        ],
        "InferenceHistory:orchestration": {
            "gpu": {"observed_gpu_hours": 0.5, "busy_gpu_hours": 0.25},
            "leases": {
                "wait_seconds": {"p50": 4.0},
                "occupancy": 0.6,
                "end_reasons": {"expired": 2, "released": 1},
                "extra_gpus_reclaimed": 3,
                "max_gpus_per_lease": 2,
            },
            "submissions": {
                "count": submissions,
                "valid": 1,
                "first_valid_seconds": 50.0,
                "best_in_run_speedup": 2.5,
                "final_in_run_result": {"speedup": 2.4},
            },
            "oom_events": 1,
        },
    }


ARGS = {
    "agents": 2,
    "token_budget": {"mode": "shared", "tokens": 100},
    "orchestration": {"workspace_gpus": 1},
    "scenarios": "A",
    "seed_pairs": [[21, 1337]],
}


def test_row_marks_status_and_flattens_measurements() -> None:
    """Mark finite, NaN and missing scores, compute token overshoot and label the solo mode."""
    scored = script.row(
        ARGS, {"run": "x", "repeat": 0}, store(), {"speedup": 2.0}, False
    )
    assert scored["status"] == "scored" and scored["tokens"] == 30
    assert scored["proxy_speedups"] == [1.5] and scored["contended_checks"] == 1
    assert scored["revoked_leases"] == 2 and scored["overshoot_tokens"] == 0
    assert scored["extra_gpus_reclaimed"] == 3 and scored["max_gpus_per_lease"] == 2
    assert scored["final_in_run_speedup"] == 2.4
    split = {**ARGS, "token_budget": {"mode": "split", "tokens": 25}}
    assert script.row(split, {}, store(), {"speedup": 2.0}, False)[
        "overshoot_tokens"
    ] == (30 - 24)
    nan = script.row(ARGS, {}, store(), {"speedup": math.nan}, False)
    assert nan["status"] == "unscored"
    assert script.row(ARGS, {}, store(), None, True)["speedup"] is None
    solo = script.row({**ARGS, "agents": 1}, {}, store(), {"speedup": 1.0}, False)
    assert solo["budget_mode"] == "solo"


def test_series_gives_duplicates_their_original_grade() -> None:
    """List each submission's measures and grade, following a duplicate to its original."""
    items = [
        {
            "number": 1,
            "same_as": None,
            "measures": {"seconds_since_release": 10.0, "cumulative_agent_tokens": 5},
            "result": {"outcome": "valid", "speedup": 1.8},
        },
        {
            "number": 2,
            "same_as": 1,
            "measures": {"seconds_since_release": 20.0},
            "result": None,
        },
        {"number": 3, "same_as": None, "measures": {}, "result": None},
    ]
    assert script.series(items) == [
        {
            "number": 1,
            "seconds_since_release": 10.0,
            "cumulative_agent_tokens": 5,
            "outcome": "valid",
            "speedup": 1.8,
        },
        {
            "number": 2,
            "seconds_since_release": 20.0,
            "outcome": "valid",
            "speedup": 1.8,
        },
        {"number": 3, "outcome": None, "speedup": None},
    ]


def test_collect_reads_every_sample_with_its_run_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn each logged sample into a row and parse the repeat number from text metadata."""
    sample = SimpleNamespace(
        scores={"inference_team_speedup": SimpleNamespace(value={"speedup": 2.0})},
        store=store(),
        error=None,
    )
    failed = SimpleNamespace(scores=None, store={}, error="pod lost")
    log = SimpleNamespace(
        eval=SimpleNamespace(metadata={"run": "r", "repeat": "1"}, task_args=ARGS),
        samples=[sample, failed],
    )
    monkeypatch.setattr(script, "read_eval_log", lambda path: log)
    rows = script.collect([Path("a.eval")])
    assert [r["status"] for r in rows] == ["scored", "error"]
    assert rows[0]["repeat"] == 1 and rows[0]["run"] == "r"
