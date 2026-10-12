"""Run authored target-specific checkpoint QA only on a reviewed GitHub Linux runner."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def active_target_containers(pins):
    """Bind live container ownership to exact target image refs/config IDs rather than names."""
    configs = set()
    for ref in pins.values():
        images = json.loads(
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout
        )
        configs.add(images[0]["Id"])
    ids = subprocess.run(
        ["docker", "ps", "--quiet"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.splitlines()
    records = []
    for container in ids:
        item = json.loads(
            subprocess.run(
                ["docker", "inspect", container],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout
        )[0]
        if item["Config"]["Image"] in pins.values() or item["Image"] in configs:
            records.append(
                {
                    "id": item["Id"],
                    "name": item["Name"],
                    "image_ref": item["Config"]["Image"],
                    "image_config_id": item["Image"],
                    "running": item["State"]["Running"],
                }
            )
    return records


def main() -> None:
    """Prepare a disposable fixture and invoke initial/resume in distinct Python processes."""
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Remote QA only; no local Docker"
    target = os.environ["MIRRORCODE_QA_TARGET"]
    assert target in {"cprepro", "sed"}
    repo = Path.cwd()
    output = repo / "run-artifacts" / f"remote-{target}-checkpoint-qa"
    output.mkdir(parents=True, exist_ok=True)
    pins = json.loads(Path(os.environ["MIRRORCODE_QA_IMAGE_PINS"]).read_text())
    assert len(pins) == 3 and all("@sha256:" in value for value in pins.values())
    assert active_target_containers(pins) == [], (
        "Dedicated QA runner must start with no target containers"
    )
    source = (repo / "src/collaboration_index/checkpoint_qa.py").read_text()
    assert source.count('target="ruff"') == 1
    source = source.replace('target="ruff"', f'target="{target}", image_pins={pins!r}')
    assert source.count("src/ruff") == 3
    source = source.replace("src/ruff", f"src/{target}")
    fixture = output / "target_qa.py"
    fixture.write_text(source)
    fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    environment = os.environ.copy()
    environment["QA_FIXTURE"] = str(fixture)
    environment["QA_OUTPUT"] = str(output)
    initial = "from inspect_ai import eval; import os; eval(os.environ['QA_FIXTURE']+'@checkpoint_ruff_qa',task_args={'mode':'deterministic','artifact_dir':os.environ['QA_OUTPUT']+'/artifacts'},log_dir=os.environ['QA_OUTPUT']+'/initial',display='none')"
    subprocess.run(
        [sys.executable, "-c", initial], env=environment, check=True, timeout=1200
    )
    logs = list((output / "initial").glob("*.eval"))
    assert len(logs) == 1
    assert active_target_containers(pins) == [], (
        "Initial physical attempt cleanup required before retry"
    )
    environment["QA_INITIAL"] = str(logs[0])
    from inspect_ai.log import read_eval_log

    original = read_eval_log(logs[0])
    assert original.status == "error"
    assert original.samples[0].store.get("checkpoint_qa_injected_failure") is True
    assert sum(e.event == "checkpoint" for e in original.samples[0].events) >= 5
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == fixture_hash
    resume = "from inspect_ai import eval_retry; import os; eval_retry(os.environ['QA_INITIAL'],log_dir=os.environ['QA_OUTPUT']+'/resume',display='none')"
    subprocess.run(
        [sys.executable, "-c", resume], env=environment, check=True, timeout=1200
    )
    final_logs = list((output / "resume").glob("*.eval"))
    assert len(final_logs) == 1
    final = read_eval_log(final_logs[0])
    assert final.status == "success"
    sample = final.samples[0]
    restored = sample.store["checkpoint_qa_restored"]
    assert set(restored) == {"agent_0", "agent_1"}
    assert all(
        all(
            p[key] is True
            for key in (
                "fresh_process",
                "fresh_sandbox",
                "private_hash_verified",
                "file_verified",
                "board_verified",
            )
        )
        for p in restored.values()
    )
    assert (
        sample.store["checkpoint_qa_initial_calls"]["agent_0"]
        == sample.store["checkpoint_qa_calls"]["agent_0"]
        == 4
    )
    peers = sample.store["TeamHistory:peers"]
    assert all(p["status"] == "limited" and p["tokens"] == 240 for p in peers)
    assert sample.store["TeamHistory:end_reason"] == "peers_finished"
    tools = [e for e in sample.events if e.event == "tool"]
    assert all(e.error is None and e.function != "submit" for e in tools)
    assert any(e.function == "evaluate_testcases" for e in tools)
    rates = sample.scores["mirrorcode_scorer"].metadata["pass_rates"]["default"]
    expected = {
        "cprepro": {"all": 1310, "visible": 680, "hidden": 630},
        "sed": {"all": 794, "visible": 470, "hidden": 324},
    }[target]
    assert {k: rates[k]["total"] for k in expected} == expected
    final_cases = [
        v["cases"] for k, v in sample.metadata.items() if k.startswith("scored_cases_")
    ]
    assert len(final_cases) == 1 and len(final_cases[0]) == expected["all"]
    assert all(
        case["expected"] is not None and case["actual"] is not None
        for case in final_cases[0]
    )
    assert sample.metadata["image_pins"] == final.eval.metadata["image_pins"] == pins
    assert len(sample.metadata["effective_images"]) == 7
    assert set(sample.metadata["effective_images"].values()) == set(pins.values())
    assert final.eval.metadata["mirrorcode_target"] == target
    assert final.eval.metadata["mirrorcode_language"] == "python"
    assert final.eval.metadata["scoring_pipelines"] == 2
    assert sample.metadata["grading_cleanup_missing_paths_only"] is True
    assert sample.metadata["checkpoint_host_backup"]["restic_version"] == "0.19.1"
    active = active_target_containers(pins)
    assert active == [], "Exact pinned target image containers remain active"
    proof = {
        "passed": True,
        "target": target,
        "cases": expected,
        "fixture_sha256": fixture_hash,
        "initial_log_sha256": hashlib.sha256(logs[0].read_bytes()).hexdigest(),
        "final_log_sha256": hashlib.sha256(final_logs[0].read_bytes()).hexdigest(),
        "restored": restored,
        "peer_tokens": [p["tokens"] for p in peers],
        "active_owned_containers": 0,
        "cleanup_bound_to_exact_image_refs_and_config_ids": True,
        "target_image_refs": sorted(pins.values()),
        "finished_peer_calls_before": sample.store["checkpoint_qa_initial_calls"][
            "agent_0"
        ],
        "finished_peer_calls_after": sample.store["checkpoint_qa_calls"]["agent_0"],
        "synthetic_provider": True,
        "resource_measurements": "Native checkpoint_qa.resource_counters retained; not a capacity proof",
    }
    (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
