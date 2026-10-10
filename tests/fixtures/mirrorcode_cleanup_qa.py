"""Inject an authored disappearing descendant through real Ruff grading containers."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ChatMessageTool, ModelOutput, ModelUsage, get_model
from inspect_ai.solver import chain, solver
from inspect_ai.util import sandbox

import collaboration_index.mirrorcode.cleanup as repair
from collaboration_index.mirrorcode import mirrorcode

ROOT = Path(os.environ["COLLAB_CLEANUP_QA_DIR"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
INJECTION = '''
_fixture_first_cleanup = True
_fixture_original_cleanup = cleanup_files

def cleanup_files(base_dir):
    """Force one vanished descendant at the actual shutil.rmtree unlink boundary."""
    global _fixture_first_cleanup
    if _fixture_first_cleanup:
        _fixture_first_cleanup = False
        child = base_dir / "authored-cleanup-race"
        child.mkdir()
        (child / "vanished-leaf").write_text("harmless fixture")
        original_unlink = os.unlink
        def vanish(path, *args, **kwargs):
            """Delete the observed fixture first, then reproduce the stale unlink."""
            if str(path) == "vanished-leaf":
                os.unlink = original_unlink
                original_unlink(path, *args, **kwargs)
                Path("/tmp/cleanup-race-injected").write_text("1")
            return original_unlink(path, *args, **kwargs)
        os.unlink = vanish
    return _fixture_original_cleanup(base_dir)

'''
original_repair = repair.repair_cleanup_script


def injected(content):
    """Add authored fault injection after the exact maintained cleanup repair."""
    return original_repair(content).replace(
        'if __name__ == "__main__":', INJECTION + 'if __name__ == "__main__":'
    )


repair.repair_cleanup_script = injected
written = asyncio.Event()


async def reply(messages, tools, choice, config):
    """Drive two harmless peers through actual testing and native finalization."""
    actor = next(m for m in messages if (m.metadata or {}).get("team_actor")).metadata[
        "team_actor"
    ]
    done = [m for m in messages if isinstance(m, ChatMessageTool)]
    assert all(m.error is None for m in done), "Authored tool error"
    if actor == "agent_0" and not done:
        function, args = (
            "bash",
            {
                "cmd": "mkdir -p src/ruff; touch src/ruff/__init__.py; printf 'raise SystemExit(1)\\n' > src/ruff/__main__.py"
            },
        )
    elif not any(m.function == "evaluate_testcases" for m in done):
        if actor == "agent_0":
            written.set()
        await asyncio.wait_for(written.wait(), 60)
        function, args = "evaluate_testcases", {}
    else:
        function, args = "message_board", {"action": "read"}
    output = ModelOutput.for_tool_call("mockllm/model", function, args)
    output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
    return output


@solver
def verify_injections():
    """Require actual reference and both agent containers to have hit the race."""

    async def solve(state, generate):
        """Read only authored count markers from the three grading sandboxes."""
        for name in (
            "reference-scoring",
            "agent-scoring-visible",
            "agent-scoring-hidden",
        ):
            assert (await sandbox(name).read_file("/tmp/cleanup-race-injected")) == "1"
        state.metadata["authored_cleanup_injected_services"] = 3
        return state

    return solve


task = mirrorcode(
    agents=2,
    target="ruff",
    token_limit_per_agent=120,
    artifact_dir=str(ROOT / "artifacts"),
    agents_per_scoring_pipeline=8,
)
spec = yaml.safe_load(Path(str(task.dataset[0].sandbox.config)).read_text())
for service in spec["services"].values():
    service.pop("build", None)
compose = ROOT / "compose.yaml"
compose.write_text(yaml.safe_dump(spec))
task.dataset[0].sandbox = task.dataset[0].sandbox.model_copy(
    update={"config": str(compose)}
)

task.solver = chain(task.solver, verify_injections())
[log] = inspect_eval(
    task,
    model=get_model("mockllm/model", custom_outputs=reply),
    log_dir=str(ROOT),
    display="none",
)
assert log.status == "success", "Authored Docker QA failed"
s = log.samples[0]
assert s.error is None
cases = [
    len(v["cases"]) for k, v in s.metadata.items() if k.startswith("scored_cases_")
]
assert cases == [822]
tools = [e for e in s.events if e.event == "tool"]
assert all(e.error is None for e in tools)
assert sum(e.function == "evaluate_testcases" for e in tools) == 2
assert s.store["TeamHistory:end_reason"] == "peers_finished"
peers = s.store["TeamHistory:peers"]
assert len(peers) == 2
proof = {
    "passed": True,
    "graded_cases": cases,
    "testing_calls": 2,
    "tool_errors": 0,
    "authored_injected_grading_services": s.metadata[
        "authored_cleanup_injected_services"
    ],
    "peer_statuses": [p["status"] for p in peers],
    "peer_tokens": [p["tokens"] for p in peers],
    "cleanup_repair_metadata": s.metadata["grading_cleanup_missing_paths_only"],
    "log_path": str(log.location),
    "log_sha256": hashlib.sha256(Path(log.location).read_bytes()).hexdigest(),
}
(ROOT / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
print(json.dumps(proof))
