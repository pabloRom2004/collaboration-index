"""Verify reserved-input compaction and checkpoint restore through real Ruff Docker."""

import json
import os
from pathlib import Path

import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai import eval_retry, task
from inspect_ai.model import ModelAPI, ModelOutput, ModelUsage, modelapi
from inspect_ai.util import ArchiveSnapshots, SandboxSnapshotConfig, sandbox
from mirrorcode_context_fixture_state import STATE

from collaboration_index.checkpoints import TeamCheckpoints
from collaboration_index.mirrorcode import mirrorcode
from collaboration_index.model_context import ContextEnvelope, estimate_envelope

ROOT = Path(os.environ["COLLAB_CONTEXT_QA_DIR"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
original_save = TeamCheckpoints.save


async def counted_save(self, hold=False):
    """Count only committed native checkpoints in the authored fixture."""
    await original_save(self, hold)
    STATE.saves += 1


@modelapi(name="mirrorcode_context_fixture")
class Fixture(ModelAPI):
    """Drive harmless oversized real tool results and one checkpointed interruption."""

    async def generate(self, input, tools, tool_choice, config):
        """Require valid complete envelopes before emitting deterministic fixture actions."""
        from inspect_ai.model import get_model

        count = await estimate_envelope(get_model(), input, tools)
        limit = 78643 if tools else 104858
        assert count <= limit and config.max_tokens == 943718
        STATE.requests.append(
            {
                "summary": not tools,
                "estimated_input_tokens": count,
                "output_reservation": config.max_tokens,
            }
        )
        if not tools:
            STATE.summary_calls += 1
            output = ModelOutput.from_content(
                self.model_name,
                "Authored context fixture summary: the fixed Ruff files and registered board remain available; continue the fixture.",
            )
        else:
            actor = next(
                m for m in input if (m.metadata or {}).get("team_actor")
            ).metadata["team_actor"]
            step = STATE.steps.get(actor, 0)
            if STATE.saves >= 5 and step >= 5 and not STATE.failed:
                STATE.failed = True
                raise RuntimeError("Authored reserved-context checkpoint interruption")
            if STATE.failed and actor not in STATE.restored:
                assert (
                    await sandbox().read_file("/workdir/context-proof.txt")
                    == "authored context fixture"
                )
                STATE.restored.add(actor)
            STATE.steps[actor] = step + 1
            name, arguments = "message_board", {"action": "read"}
            if step == 0:
                arguments = {"action": "register", "name": "Context fixture " + actor}
            elif step == 1:
                name, arguments = (
                    "bash",
                    {
                        "cmd": "mkdir -p src/ruff; touch src/ruff/__init__.py; printf 'raise SystemExit(1)\\n' > src/ruff/__main__.py; printf 'authored context fixture' > /workdir/context-proof.txt"
                    },
                )
            elif step == 2:
                name, arguments = (
                    "bash",
                    {
                        "cmd": "python3 -c \"print('authored oversized tool fixture ' * 50000)\""
                    },
                )
            elif step == 3:
                name, arguments = "evaluate_testcases", {}
            output = ModelOutput.for_tool_call(self.model_name, name, arguments)
        output.usage = ModelUsage(input_tokens=30, output_tokens=20, total_tokens=50)
        return output

    def max_tokens(self):
        """Expose the fixture's unchanged complete output allowance."""
        return 943718


@task
def context_ruff_fixture():
    """Create the same two-peer Docker task with opt-in input reservation and durable state."""
    work = ROOT / "context-proof"
    work.mkdir(exist_ok=True)
    result = mirrorcode(
        agents=2,
        target="ruff",
        language="python",
        token_limit_per_agent=1000,
        artifact_dir=str(work),
        agents_per_scoring_pipeline=1,
        checkpoint_enabled=True,
        checkpoint_interval_seconds=0.01,
        context_window=1048576,
        reserve_output_context=True,
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
    TeamCheckpoints.save = counted_save
    original = inspect_eval(
        context_ruff_fixture(),
        model="mirrorcode_context_fixture/model",
        max_tokens=943718,
        max_tool_output=2000000,
        log_dir=str(ROOT / "context-proof"),
        display="none",
        log_level="error",
    )[0]
    assert original.status == "error" and STATE.failed and STATE.saves >= 5
    original_events = original.samples[0].events
    compactions_before = sum(e.event == "compaction" for e in original_events)
    assert compactions_before >= 2 and STATE.summary_calls >= 2
    resumed = eval_retry(
        original,
        display="none",
        log_level="error",
        log_dir=str(ROOT / "context-proof"),
        incomplete_action="error",
    )[0]
    sample = resumed.samples[0]
    assert resumed.status == "success" and sample.error is None
    peers = sample.store["TeamHistory:peers"]
    assert len(peers) == 2 and all(
        p["status"] == "limited" and p["tokens"] >= 1000 for p in peers
    )
    cases = [
        len(value["cases"])
        for key, value in sample.metadata.items()
        if key.startswith("scored_cases_")
    ]
    assert cases == [822] and len(STATE.restored) == 2
    state_path = (
        Path(resumed.location).with_suffix(".checkpoints")
        / "ruff_python__1/context/agent_state.json"
    )
    saved = json.loads(state_path.read_text())
    assert {"peer:agent_0:compaction", "peer:agent_1:compaction"} <= set(saved)
    policy = ContextEnvelope(1048576, 943718, 0.75)
    proof = {
        "passed": True,
        "real_model_calls": 0,
        "real_docker": True,
        "native_context_policy": policy.metadata(),
        "summary_calls": STATE.summary_calls,
        "committed_checkpoints": STATE.saves,
        "compactions_before_interruption": compactions_before,
        "restored_file_peers": len(STATE.restored),
        "graded_cases": cases,
        "board_embedded": isinstance(sample.store["BoardHistory:journal"], list),
        "all_request_envelopes_valid": all(
            row["estimated_input_tokens"] + row["output_reservation"] <= 1048576
            for row in STATE.requests
        ),
        "native_peer_tokens": [p["tokens"] for p in peers],
        "native_peer_limits": [p["status"] for p in peers],
        "max_summary_estimated_input": max(
            row["estimated_input_tokens"] for row in STATE.requests if row["summary"]
        ),
        "max_ordinary_estimated_input": max(
            row["estimated_input_tokens"]
            for row in STATE.requests
            if not row["summary"]
        ),
    }
    assert proof["all_request_envelopes_valid"] and proof["board_embedded"]
    (ROOT / "context-docker-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))
