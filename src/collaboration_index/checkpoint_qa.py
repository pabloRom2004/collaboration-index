"""Development-only deterministic and real-provider durable MirrorCode restore probes."""

import hashlib
import json
import uuid
from typing import Any

from inspect_ai import Task, task
from inspect_ai.model import ModelAPI, ModelOutput, ModelUsage, get_model, modelapi
from inspect_ai.util import sandbox

from collaboration_index.checkpoints import OWNER, TeamCheckpoints
from collaboration_index.mirrorcode import mirrorcode

PROCESS_ID = uuid.uuid4().hex
ORIGINAL_SAVE = getattr(TeamCheckpoints, "_qa_original_save", TeamCheckpoints.save)
ORIGINAL_GENERATION = getattr(
    TeamCheckpoints, "_qa_original_generation", TeamCheckpoints.generation
)


def digest(value: Any) -> str:
    """Hash private fixture state without exposing its contents in proof metadata."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def probe_save(self: Any, hold: bool = False) -> None:
    """Capture bounded proof at the frozen native boundary and fail only the initial attempt."""
    native = self.cp.checkpoint

    async def commit() -> None:
        """Store exact private-history hashes and file identities before native durable save."""
        store = self.task.store
        proof = store.get("checkpoint_qa", {})
        proof["commits"] = proof.get("commits", 0) + 1
        proof["process_id"] = PROCESS_ID
        proof["sandbox_hostname"] = (await sandbox().exec(["hostname"])).stdout.strip()
        resources = await sandbox().exec(
            [
                "bash",
                "-c",
                'for p in /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat; do if test -r "$p"; then cat "$p"; fi; done; du -sk /workdir /workspace /root /home/coder 2>/dev/null || true',
            ]
        )
        proof["resource_counters"] = resources.stdout
        proof["calls"] = store.get("checkpoint_qa_calls", {})
        proof["private_hashes"] = {
            actor: digest([m.model_dump(mode="json") for m in messages])
            for actor, messages in self.snapshot.messages.items()
        }
        proof["usage"] = dict(self.snapshot.usages)
        proof["board_hash"] = self.snapshot.board["sha256"]
        proof["file_hash"] = hashlib.sha256(
            (await sandbox().read_file("/workdir/checkpoint-qa-marker")).encode()
        ).hexdigest()
        store.set("checkpoint_qa", proof)
        await native()

    self.cp.checkpoint = commit
    try:
        await ORIGINAL_SAVE(self, hold)
    finally:
        self.cp.checkpoint = native
    proof = self.task.store.get("checkpoint_qa", {})
    minimum = 5 if self.task.metadata["checkpoint_qa_mode"] == "deterministic" else 2
    if self.cp.attempt == "initial" and proof.get("commits", 0) >= minimum:
        self.task.store.set("checkpoint_qa_injected_failure", True)
        raise RuntimeError(
            "Authored post-checkpoint failure; resume this exact eval-set"
        )


def probe_generation(self: Any, actor: str, threshold: Any, retry_refusals: Any) -> Any:
    """Verify restored private state before delegating unchanged production generation."""
    original = ORIGINAL_GENERATION(self, actor, threshold, retry_refusals)
    checked = False

    async def generate(current: Any, tools: Any) -> Any:
        """Validate exact private checkpoint and fresh-process identity before any model call."""
        nonlocal checked
        if not checked:
            checked = True
            if self.cp.attempt != "initial":
                proof = self.task.store.get("checkpoint_qa")
                assert proof["process_id"] != PROCESS_ID, (
                    "Hawk proof requires a fresh controller process"
                )
                hostname = (await sandbox().exec(["hostname"])).stdout.strip()
                assert hostname != proof["sandbox_hostname"], (
                    "Hawk proof requires a fresh sandbox"
                )
                self.task.store.set("checkpoint_qa_initial_calls", proof["calls"])
                assert self.snapshot.board["sha256"] == proof["board_hash"]
                assert (
                    hashlib.sha256(
                        (
                            await sandbox().read_file("/workdir/checkpoint-qa-marker")
                        ).encode()
                    ).hexdigest()
                    == proof["file_hash"]
                )
                restored = self.task.store.get("checkpoint_qa_restored", {})
                for restored_actor, messages in self.snapshot.messages.items():
                    assert (
                        digest([m.model_dump(mode="json") for m in messages])
                        == proof["private_hashes"][restored_actor]
                    )
                    assert (
                        self.snapshot.usages[restored_actor]
                        == proof["usage"][restored_actor]
                    )
                    restored[restored_actor] = {
                        "private_hash_verified": True,
                        "usage": self.prior[restored_actor],
                        "file_verified": True,
                        "board_verified": True,
                        "fresh_process": True,
                        "fresh_sandbox": True,
                    }
                self.task.store.set("checkpoint_qa_restored", restored)
        return await original(current, tools)

    return generate


@modelapi(name="checkpoint_fixture")
class CheckpointFixture(ModelAPI):
    """Exercise harmless board and workspace operations without contacting a provider."""

    async def generate(
        self, input: Any, tools: Any, tool_choice: Any, config: Any
    ) -> ModelOutput:
        """Choose deterministic actions from restored native private tool history."""
        actor = next(m for m in input if (m.metadata or {}).get("team_actor")).metadata[
            "team_actor"
        ]
        index = int(actor.split("_")[-1])
        owner = OWNER.get()
        calls = owner.task.store.get("checkpoint_qa_calls", {})
        calls[actor] = calls.get(actor, 0) + 1
        owner.task.store.set("checkpoint_qa_calls", calls)
        step = sum(m.role == "tool" for m in input)
        function, args = "message_board", {"action": "read"}
        if step == 0:
            args = {"action": "register", "name": f"Fixture {index}"}
        elif step == 1:
            args = {"action": "send", "message": f"Authored fixture {index}"}
        elif step == 2 and index == 0:
            function, args = (
                "bash",
                {
                    "cmd": "mkdir -p src/ruff; touch src/ruff/__init__.py; printf 'raise SystemExit(1)\\n' > src/ruff/__main__.py"
                },
            )
        elif step == 4 and index == 1:
            function, args = "evaluate_testcases", {}
        out = ModelOutput.for_tool_call(self.model_name, function, args)
        amount = 60 if index == 0 else 30
        out.usage = ModelUsage(
            input_tokens=amount // 2, output_tokens=amount // 2, total_tokens=amount
        )
        return out

    def max_tokens(self) -> int:
        """Expose the deterministic fixture output limit."""
        return 128000


@task
def checkpoint_ruff_qa(
    mode: str = "deterministic",
    artifact_dir: str = "/tmp/checkpoint-qa",
    token_limit_per_agent: int | None = None,
    agents: int = 2,
) -> Task:
    """Build a development-only production Ruff task with a once-per-initial-attempt fault."""
    if mode not in {"deterministic", "provider"}:
        raise ValueError("Unknown checkpoint QA mode")
    setattr(TeamCheckpoints, "_qa_original_save", ORIGINAL_SAVE)
    setattr(TeamCheckpoints, "_qa_original_generation", ORIGINAL_GENERATION)
    setattr(TeamCheckpoints, "save", probe_save)
    setattr(TeamCheckpoints, "generation", probe_generation)
    result = mirrorcode(
        agents=agents,
        target="ruff",
        language="python",
        artifact_dir=artifact_dir,
        token_limit_per_agent=token_limit_per_agent
        or (240 if mode == "deterministic" else 20000),
        checkpoint_enabled=True,
        checkpoint_interval_seconds=0.01,
        agents_per_scoring_pipeline=8 if agents == 64 else 1,
        context_window=1050000,
        compaction_threshold=0.75,
    )
    result.metadata = {**(result.metadata or {}), "checkpoint_qa_mode": mode}
    result.dataset[0].metadata = {
        **(result.dataset[0].metadata or {}),
        "checkpoint_qa_mode": mode,
    }
    result.fail_on_error = True
    from inspect_ai.solver import Solver, solver

    @solver
    def fixture_files() -> Solver:
        """Install one harmless durable marker before any private peer work."""

        async def solve(state: Any, generate: Any) -> Any:
            """Write an authored marker whose hash must survive the fresh sandbox restore."""
            await sandbox().write_file(
                "/workdir/checkpoint-qa-marker", "Authored checkpoint QA marker"
            )
            return state

        return solve

    assert result.setup is not None
    result.setup = (
        [*result.setup, fixture_files()]
        if isinstance(result.setup, list)
        else [result.setup, fixture_files()]
    )
    if mode == "provider":
        assert isinstance(result.dataset[0].input, str)
        result.dataset[
            0
        ].input += "\nThis is infrastructure QA. Begin by registering a unique board display name, sending one harmless QA message and reading the board. Write a harmless marker file in /workdir and call evaluate_testcases. Continue working within your native token allowance after a restore.\n"
    if mode == "deterministic":
        result.model = get_model("checkpoint_fixture/model")
    return result
