"""Exercise native MirrorCode checkpoint recovery through real Ruff Docker."""

import json
import os
from pathlib import Path

import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai import eval_retry, task
from inspect_ai.model import ModelAPI, ModelOutput, ModelUsage, modelapi
from inspect_ai.util import ArchiveSnapshots, SandboxSnapshotConfig, sandbox
from mirrorcode_checkpoint_fixture_state import STATE

from collaboration_index.checkpoints import TeamCheckpoints
from collaboration_index.mirrorcode import mirrorcode

ROOT = Path(os.environ["COLLAB_CHECKPOINT_QA_DIR"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
original_save = TeamCheckpoints.save


async def counted_save(self, hold=False):
    """Record successful committed boundaries without examining participant content."""
    original_checkpoint = self.cp.checkpoint

    async def verified_checkpoint():
        """Assert the authored background writer stays stopped across the native snapshot."""
        import asyncio

        before = await sandbox().exec(
            ["bash", "-c", "cat /workdir/writer-count 2>/dev/null || true"]
        )
        await asyncio.sleep(0.1)
        after = await sandbox().exec(
            ["bash", "-c", "cat /workdir/writer-count 2>/dev/null || true"]
        )
        assert before.stdout == after.stdout
        if before.stdout:
            STATE.writer_frozen_checks += 1
            STATE.writer_saved = before.stdout
        await original_checkpoint()

    self.cp.checkpoint = verified_checkpoint
    try:
        await original_save(self, hold)
    finally:
        self.cp.checkpoint = original_checkpoint
    STATE.saves += 1


if __name__ == "__main__":
    TeamCheckpoints.save = counted_save


@modelapi(name="mirrorcode_finished_fixture")
class Fixture(ModelAPI):
    """Drive authored harmless board and Ruff operations with one forced interruption."""

    async def generate(self, input, tools, tool_choice, config):
        """Choose a deterministic fixture action from the restored tool count."""
        if STATE.saves >= 5 and not STATE.failed:
            STATE.failed = True
            raise RuntimeError("Authored checkpoint interruption")
        actor = next(m for m in input if (m.metadata or {}).get("team_actor")).metadata[
            "team_actor"
        ]
        index = int(actor.split("_")[-1])
        if STATE.failed and actor not in STATE.restored:
            file = await sandbox().read_file("/workdir/checkpoint-proof.txt")
            assert file == "authored durable fixture"
            writer = await sandbox().read_file("/workdir/writer-count")
            assert writer == STATE.writer_saved
            STATE.restored.add(actor)
        step = sum(m.role == "tool" for m in input)
        STATE.calls.append({"actor": actor, "history_length": len(input), "step": step})
        function, args = "message_board", {"action": "read"}
        if step == 0:
            args = {"action": "register", "name": f"Fixture {index}"}
        elif step == 1:
            args = {"action": "send", "message": f"Authored checkpoint fixture {index}"}
        elif step == 2 and index == 0:
            function, args = (
                "bash",
                {
                    "cmd": "mkdir -p src/ruff; touch src/ruff/__init__.py; printf 'raise SystemExit(1)\\n' > src/ruff/__main__.py; printf 'authored durable fixture' > /workdir/checkpoint-proof.txt\ncat > /workdir/fixture-writer.py <<'WRITER'\nimport time\nfrom pathlib import Path\np=Path('/workdir/writer-count')\ni=0\nwhile True:\n i+=1\n p.write_text(str(i))\n time.sleep(.01)\nWRITER\nnohup python3 /workdir/fixture-writer.py >/dev/null 2>&1 </dev/null &"
                },
            )
        elif step == 4:
            function, args = "evaluate_testcases", {}
        output = ModelOutput.for_tool_call(self.model_name, function, args)
        output.usage = ModelUsage(
            input_tokens=40 if index == 0 else 20,
            output_tokens=20 if index == 0 else 10,
            total_tokens=60 if index == 0 else 30,
        )
        return output

    def max_tokens(self):
        """Expose the fixture model output limit."""
        return 1000


@task
def checkpoint_ruff_fixture():
    """Reconstruct the same two-peer task and local snapshot strategy on retry."""
    work = ROOT / "finished-peer-proof"
    work.mkdir(exist_ok=True)
    result = mirrorcode(
        agents=2,
        target="ruff",
        language="python",
        token_limit_per_agent=240,
        artifact_dir=str(work),
        agents_per_scoring_pipeline=1,
        checkpoint_enabled=True,
        checkpoint_interval_seconds=0.01,
    )
    sample = result.dataset[0]
    compose = yaml.safe_load(Path(str(sample.sandbox.config)).read_text())
    for service in compose["services"].values():
        service.pop("build", None)
    path = work / "compose.yaml"
    path.write_text(yaml.safe_dump(compose))
    sample.sandbox = sample.sandbox.model_copy(update={"config": str(path)})
    result.checkpoint.sandbox_paths["default"] = SandboxSnapshotConfig(
        paths=["/workdir", "/workspace", "/root", "/home/coder"],
        strategy=ArchiveSnapshots(),
    )
    return result


if __name__ == "__main__":
    original = inspect_eval(
        checkpoint_ruff_fixture(),
        model="mirrorcode_finished_fixture/model",
        log_dir=str(ROOT / "finished-peer-proof"),
        display="none",
        log_level="error",
    )[0]
    print(
        json.dumps(
            {
                "phase": "interrupted",
                "status": original.status,
                "saves": STATE.saves,
                "error_type": type(original.error).__name__ if original.error else None,
            }
        )
    )
    assert original.status == "error" and STATE.saves >= 5
    finished_before = sum(c["actor"] == "agent_0" for c in STATE.calls)
    assert original.samples[0].store["TeamHistory:peers"][0]["status"] == "limited"
    resumed = eval_retry(
        original,
        display="none",
        log_level="error",
        log_dir=str(ROOT / "finished-peer-proof"),
        incomplete_action="error",
    )[0]
    s = resumed.samples[0]
    print(
        json.dumps(
            {
                "phase": "resumed",
                "status": resumed.status,
                "error": s.error.message[:500] if s.error else None,
            }
        )
    )
    assert resumed.status == "success" and s.error is None
    assert sum(c["actor"] == "agent_0" for c in STATE.calls) == finished_before
    peers = s.store["TeamHistory:peers"]
    assert all(p["tokens"] == 240 and p["status"] == "limited" for p in peers)
    cases = [
        len(v["cases"]) for k, v in s.metadata.items() if k.startswith("scored_cases_")
    ]
    assert cases == [822]
    assert isinstance(s.store["BoardHistory:journal"], list)
    saved = json.loads(
        (
            Path(resumed.location).with_suffix(".checkpoints")
            / "ruff_python__1/context/agent_state.json"
        ).read_text()
    )
    assert {"peer:agent_0:compaction", "peer:agent_1:compaction"} <= set(saved)
    proof = {
        "finished_compaction_retained": True,
        "passed": True,
        "agents": 2,
        "graded_cases": cases,
        "peer_tokens": [p["tokens"] for p in peers],
        "saves": STATE.saves,
        "physical_model_calls": len(STATE.calls),
        "restored_file_peers": len(STATE.restored),
        "frozen_writer_checks": STATE.writer_frozen_checks,
        "logical_tokens": sum(v.total_tokens for v in s.model_usage.values()),
        "failed_log": original.location,
        "resumed_log": resumed.location,
        "board_embedded": True,
        "finished_peer_calls_before": finished_before,
        "finished_peer_calls_after": sum(c["actor"] == "agent_0" for c in STATE.calls),
    }
    (ROOT / "finished-peer-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))
