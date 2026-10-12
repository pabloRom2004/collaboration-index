"""Run bounded authored 64-peer target grading on a remote runner with exact existing pins."""

import ast
import asyncio
import hashlib
import importlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path
from typing import Any

from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessageTool,
    GenerateConfig,
    ModelOutput,
    ModelUsage,
    get_model,
)
from inspect_ai.solver import chain, solver
from inspect_ai.tool import ToolDef, ToolResult
from inspect_ai.util import sandbox

from collaboration_index.mirrorcode import mirrorcode


def host_capacity_record(daemon_cpus: int, requested_cpus: float) -> dict[str, Any]:
    """Describe the exact Docker CPU admissibility condition without changing its limits."""
    return {
        "docker_daemon_cpus": daemon_cpus,
        "requested_workspace_cpus": requested_cpus,
        "eligible": daemon_cpus >= requested_cpus,
        "reason": "admissible"
        if daemon_cpus >= requested_cpus
        else "workspace_cpu_quota_exceeds_docker_daemon_cpus",
        "production_workspace_cpus": 17,
        "authored_cpu_allocation_matches_production": False,
    }


def main():
    """Reuse the maintained busy fixture without any external provider inference request."""
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Remote-only authored QA"
    target = os.environ["MIRRORCODE_QA_TARGET"]
    assert target in {"cprepro", "sed"}
    pins = json.loads(Path(os.environ["MIRRORCODE_QA_IMAGE_PINS"]).read_text())
    assert len(pins) == 3
    root = Path("run-artifacts") / f"remote-{target}-native64-qa"
    root.mkdir(parents=True, exist_ok=True)
    daemon_cpus = int(
        subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    )
    capacity = host_capacity_record(daemon_cpus, 4.0)
    capacity["docker_daemon_memory_bytes"] = int(
        subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
    )
    (root / "host-capacity-preflight.json").write_text(
        json.dumps(capacity, indent=2) + "\n"
    )
    if not capacity["eligible"]:
        raise RuntimeError(
            "Authored QA workspace CPU quota exceeds Docker daemon CPUs; see host-capacity-preflight.json"
        )
    source = Path("tests/test_mirrorcode.py").read_text()
    tree = ast.parse(source)
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "busy_team_fixture"
    )
    fixture = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno]) + "\n"
    assert fixture.count("src/rev") == 3
    assert (
        fixture.count("if index < 9:") == 1
        and fixture.count("if len(graded) == 9:") == 1
    )
    assert (
        fixture.count('            if index == 0:\n                function = "submit"')
        == 1
    )
    fixture = (
        fixture.replace("src/rev", f"src/{target}")
        .replace("if index < 9:", "if index < 8:")
        .replace("if len(graded) == 9:", "if len(graded) == 8:")
    )
    fixture = fixture.replace(
        '            if index == 0:\n                function = "submit"',
        '            function, arguments = "message_board", {"action": "read"}',
    )
    fixture = fixture.replace("grading_done.wait(), 240", "grading_done.wait(), 900")
    assert fixture.count("config=GenerateConfig(max_connections=64)") == 1
    fixture = fixture.replace(
        "config=GenerateConfig(max_connections=64)",
        "config=GenerateConfig(max_connections=64, max_tokens=128000, max_retries=300, attempt_timeout=900)",
    )
    (root / "fixture-source.py").write_text(fixture)
    ast.parse(fixture)

    async def wait_for_fixture(awaitable, timeout):
        """Bound authored synchronization without real model or provider calls."""
        return await asyncio.wait_for(awaitable, timeout)

    namespace = {
        "Any": Any,
        "asyncio": asyncio,
        "ChatMessageTool": ChatMessageTool,
        "ModelOutput": ModelOutput,
        "ModelUsage": ModelUsage,
        "get_model": get_model,
        "GenerateConfig": GenerateConfig,
        "wait_for_fixture": wait_for_fixture,
        "SOLUTION": "raise SystemExit(1)\n",
    }
    exec(compile(fixture, str(root / "fixture-source.py"), "exec"), namespace)
    module = importlib.import_module("collaboration_index.mirrorcode.task")
    original = module.pooled
    active = 0
    peak = 0
    pipelines = set()
    intervals = []

    def measured_pooled(definition, free, tar_lock):
        """Measure overlapping grading inside the real allocated production pipeline context."""

        async def execute() -> ToolResult:
            """Delegate the unchanged actual testing tool while recording numeric admission evidence."""
            nonlocal active, peak
            index = module.PIPELINE.get()
            pipelines.add(index)
            started = time.monotonic()
            active += 1
            peak = max(peak, active)
            try:
                return await definition.tool()
            finally:
                active -= 1
                intervals.append(
                    {
                        "pipeline": index,
                        "started": started,
                        "finished": time.monotonic(),
                    }
                )

        measured = ToolDef(
            execute,
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
        )
        return original(measured, free, tar_lock)

    module.pooled = measured_pooled
    task = mirrorcode(
        agents=64,
        target=target,
        language="python",
        token_limit_per_agent=300,
        team_time_limit=None,
        allow_submit=False,
        artifact_dir=str(root / "artifacts"),
        agents_per_scoring_pipeline=8,
        cpus_per_agent=0.046875,
        image_pins=pins,
        checkpoint_enabled=True,
        checkpoint_interval_seconds=3600,
        context_window=1050000,
        compaction_threshold=0.75,
    )

    @solver
    def verify_markers():
        """Verify all64 harmless peer contributions before the final scorer and sandbox cleanup."""

        async def solve(state, generate):
            """Count only authored marker files in the shared target workspace."""
            count = await sandbox().exec(
                [
                    "sh",
                    "-c",
                    "find /workdir/src/fixture-markers -maxdepth 1 -type f | wc -l",
                ]
            )
            assert count.success and int(count.stdout.strip()) == 64
            state.metadata["authored_peer_marker_files"] = 64
            return state

        return solve

    task.solver = chain(task.solver, verify_markers())
    model = namespace["busy_team_fixture"]()
    started = time.monotonic()
    try:
        [log] = inspect_eval(
            task, model=model, log_dir=str(root), display="none", log_level="error"
        )
    finally:
        module.pooled = original
    assert log.status == "success", (
        "Authored64-peer QA did not reach a successful final"
    )
    log = read_eval_log(log.location)
    sample = log.samples[0]
    assert (
        sample.error is None
        and sample.store["TeamHistory:end_reason"] == "peers_finished"
    )
    assert sample.metadata["authored_peer_marker_files"] == 64
    assert len({p["sandbox_hostname"] for p in sample.store["TeamHistory:peers"]}) == 1
    peers = sample.store["TeamHistory:peers"]
    assert len(peers) == 64
    assert all(
        p["status"] == "limited" and p["tokens"] == 300 and p["tool_calls"] >= 3
        for p in peers
    )
    assert peak == 8 and pipelines == set(range(8)) and len(intervals) == 8
    tools = [e for e in sample.events if e.event == "tool"]
    assert all(e.error is None and e.function != "submit" for e in tools)
    assert sum(e.function == "evaluate_testcases" for e in tools) == 8
    totals = {
        "cprepro": {"all": 1310, "visible": 680, "hidden": 630},
        "sed": {"all": 794, "visible": 470, "hidden": 324},
    }[target]
    rates = sample.scores["mirrorcode_scorer"].metadata["pass_rates"]["default"]
    assert {k: rates[k]["total"] for k in totals} == totals
    cases = next(
        v["cases"] for k, v in sample.metadata.items() if k.startswith("scored_cases_")
    )
    assert len(cases) == totals["all"] and all(
        c["expected"] is not None and c["actual"] is not None for c in cases
    )
    assert (
        log.eval.task_version == 3
        and log.eval.metadata["team_size_disclosed"]
        and not log.eval.metadata["allow_submit"]
    )
    assert (
        log.eval.metadata["scoring_pipelines"] == 8
        and sample.metadata["image_pins"] == pins
    )
    assert len(sample.metadata["effective_images"]) == 25 and set(
        sample.metadata["effective_images"].values()
    ) == set(pins.values())
    assert (
        sample.metadata["grading_cleanup_missing_paths_only"]
        and sample.metadata["checkpoint_host_backup"]["restic_version"] == "0.19.1"
    )
    checkpoints = [e for e in sample.events if e.event == "checkpoint"]
    assert checkpoints and sample.store["team_checkpoint_commits"] > 0
    journal = sample.store["BoardHistory:journal"]
    assert (
        sum(e["kind"] == "register" for e in journal) == 64
        and sum(e["kind"] == "message" for e in journal) == 64
    )
    config_ids = {
        json.loads(
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout
        )[0]["Id"]
        for ref in pins.values()
    }
    container_ids = subprocess.run(
        ["docker", "ps", "--quiet"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.splitlines()
    remaining = []
    for container in container_ids:
        item = json.loads(
            subprocess.run(
                ["docker", "inspect", container],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout
        )[0]
        if item["Image"] in config_ids or item["Config"]["Image"] in pins.values():
            remaining.append(item["Id"])
    assert remaining == [], "Exact target-owned containers remain active"
    proof = {
        "passed": True,
        "target": target,
        "cases": totals,
        "native_peers": 64,
        "peer_caps": 300,
        "synthetic_team_tokens": 19200,
        "scoring_pipelines": 8,
        "grading_containers": 24,
        "peak_actual_testing_calls": peak,
        "pipeline_indices": sorted(pipelines),
        "testing_intervals": intervals,
        "zero_tool_testing_reference_errors": True,
        "active_owned_containers": 0,
        "checkpoint_count": len(checkpoints),
        "authored_checkpoint_interval_seconds": 3600,
        "periodic_checkpoint_admission_exercised": False,
        "forced_after_peer_join_checkpoint_verified": True,
        "checkpoint_bytes": [e.size_bytes for e in checkpoints],
        "checkpoint_duration_ms": [e.duration_ms for e in checkpoints],
        "elapsed_seconds": time.monotonic() - started,
        "controller_ru_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
        "final_log": str(log.location),
        "final_log_sha256": hashlib.sha256(Path(log.location).read_bytes()).hexdigest(),
        "image_pins": pins,
        "external_provider_requests": 0,
        "qa_cpus_per_agent": 0.046875,
        "qa_workspace_cpu_limit": 4.0,
        "production_workspace_cpu_limit": 17,
        "allocation_matches_production": False,
        "host_capacity_preflight": capacity,
        "synthetic_only": True,
        "limit": "Authored grading/packing at4CPU; not matched17CPU performance, real-model resource minima or full84-team cluster capacity",
    }
    (root / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: proof[k]
                for k in [
                    "passed",
                    "target",
                    "cases",
                    "peak_actual_testing_calls",
                    "controller_ru_maxrss_kib",
                    "elapsed_seconds",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
