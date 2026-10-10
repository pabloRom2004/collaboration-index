"""Exercise an authored peer failure while another peer waits for a checkpoint."""

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, ModelUsage, get_model
from inspect_ai.util import ArchiveSnapshots, SandboxSnapshotConfig

from collaboration_index.checkpoints import OWNER
from collaboration_index.mirrorcode import mirrorcode

ROOT = Path(os.environ["COLLAB_PEER_FAILURE_QA_DIR"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
second_entered = asyncio.Event()
MARKER = "authored primary peer failure"
primary_injected = False


async def reply(messages, tools, choice, config):
    """Fail one authored peer after its sibling reaches the safe-boundary barrier."""
    global primary_injected
    owner = OWNER.get()
    actor = next(m for m in messages if (m.metadata or {}).get("team_actor")).metadata[
        "team_actor"
    ]
    if actor == "agent_1":
        second_entered.set()
        async with asyncio.timeout(30):
            while "agent_0" not in owner.barrier.waiting:
                await asyncio.sleep(0.01)
        primary_injected = True
        raise OSError(MARKER)
    await asyncio.wait_for(second_entered.wait(), 30)
    owner.barrier.last -= 601
    output = ModelOutput.for_tool_call(
        "mockllm/model", "message_board", {"action": "register", "name": "Fixture"}
    )
    output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
    return output


evaluation = mirrorcode(
    agents=2,
    target="ruff",
    token_limit_per_agent=1000,
    artifact_dir=str(ROOT / "artifacts"),
    checkpoint_enabled=True,
    checkpoint_interval_seconds=600,
)
sample = evaluation.dataset[0]
compose = yaml.safe_load(Path(str(sample.sandbox.config)).read_text())
for service in compose["services"].values():
    service.pop("build", None)
path = ROOT / "compose.yaml"
path.write_text(yaml.safe_dump(compose))
sample.sandbox = sample.sandbox.model_copy(update={"config": str(path)})
evaluation.checkpoint.sandbox_paths["default"] = SandboxSnapshotConfig(
    paths=["/workdir", "/workspace", "/root", "/home/coder"],
    strategy=ArchiveSnapshots(),
)
log = inspect_eval(
    evaluation,
    model=get_model("mockllm/model", custom_outputs=reply),
    log_dir=str(ROOT / "native"),
    display="none",
    log_level="error",
    max_retries=0,
)[0]
assert log.status == "error"
assert primary_injected, "The authored primary failure must reach the native peer"
result = log.samples[0]
assert result.error is not None and not result.scores
source = Path(log.location)
flat = Path.cwd() / "logs" / source.name
shutil.copy2(source, flat)
proof = {
    "native_status": log.status,
    "primary_failure_preserved": MARKER in result.error.traceback,
    "primary_failure_injected": primary_injected,
    "checkpoint_abort_present": "Team checkpoint aborted" in result.error.traceback,
    "checkpoint_count": sum(e.event == "checkpoint" for e in result.events),
    "peer_count": len(result.store["TeamHistory:peers"]),
    "board_embedded": isinstance(result.store.get("BoardHistory:journal"), list),
    "flat_log_path": str(flat),
    "log_sha256": hashlib.sha256(flat.read_bytes()).hexdigest(),
    "authored_fixture_only": True,
}
(ROOT / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
print(json.dumps(proof))
