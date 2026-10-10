"""Development-only durable native compaction probe for a dedicated Hawk worker."""

import hashlib
import json
import uuid
from typing import Any, cast

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageUser,
    CompactionAuto,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    compaction,
    get_model,
    modelapi,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import CheckpointConfig, Manual, checkpointer

from collaboration_index.checkpoints import PeerCheckpointer

PROCESS = uuid.uuid4().hex


@modelapi(name="checkpoint_compaction_fixture")
class Fixture(ModelAPI):
    """Return an authored summary for native compaction without contacting a provider."""

    async def generate(
        self, input: Any, tools: Any, tool_choice: Any, config: Any
    ) -> ModelOutput:
        """Produce a harmless deterministic native summary with explicit synthetic usage."""
        output = ModelOutput.from_content(
            self.model_name, "Authored private fixture summary."
        )
        output.usage = ModelUsage(input_tokens=30, output_tokens=10, total_tokens=40)
        return output

    def max_tokens(self) -> int:
        """Expose the fixture full output allowance."""
        return 128000


@solver
def private_compaction() -> Solver:
    """Own one native checkpoint with two independent actual compaction states."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        """Save compacted message identities then verify them after fresh-process hydration."""
        async with checkpointer() as cp:
            proof = cast(
                dict[str, Any],
                cp.track("proof", lambda: proof, {}, value_type=dict[str, Any]),
            )
            histories: dict[str, list[ChatMessage]] = {}
            for actor in ["one", "two"]:
                scoped = PeerCheckpointer(cp, actor)

                def tracked_messages(actor: str = actor) -> list[ChatMessage]:
                    """Retain this fixed peer history for the next durable native checkpoint."""
                    return histories[actor]

                messages = cast(
                    list[ChatMessage],
                    scoped.track(
                        "messages",
                        tracked_messages,
                        [ChatMessageUser(content="Authored private " + actor)]
                        + [
                            ChatMessageUser(content="Harmless " + str(i) + " x " * 200)
                            for i in range(20)
                        ],
                        value_type=list[ChatMessage],
                    ),
                )
                histories[actor] = messages
                handler = compaction(
                    CompactionAuto(threshold=256),
                    prefix=messages[:1],
                    checkpointer=scoped,
                )
                compacted, _ = await handler.compact_input(
                    messages, force=cp.attempt == "initial"
                )
                ids = [m.id for m in compacted]
                digest = hashlib.sha256(
                    json.dumps(
                        [m.model_dump(mode="json") for m in compacted], sort_keys=True
                    ).encode()
                ).hexdigest()
                if cp.attempt == "initial":
                    proof[actor] = {"ids": ids, "sha256": digest}
                else:
                    assert PROCESS != proof["process"]
                    assert (
                        ids == proof[actor]["ids"] and digest == proof[actor]["sha256"]
                    )
            if cp.attempt == "initial":
                proof["process"] = PROCESS
                state.store.set(
                    "actual_compaction_baseline",
                    {
                        "private_peers": 2,
                        "compacted_hashes": {
                            actor: proof[actor]["sha256"] for actor in ["one", "two"]
                        },
                        "summary_calls": 2,
                        "cumulative_synthetic_tokens": 80,
                    },
                )
                await cp.checkpoint()
                state.store.set("fresh_compaction_injected_fault", True)
                raise RuntimeError("Authored actual-compaction checkpoint failure")
            state.store.set(
                "fresh_compaction_proof",
                {
                    "peers": 2,
                    "exact_compacted_ids": True,
                    "exact_compacted_hashes": True,
                    "fresh_process": True,
                },
            )
        return state

    return solve


@task
def checkpoint_compaction_qa() -> Task:
    """Configure sandbox-free authored native compaction with retained checkpoint storage."""
    return Task(
        dataset=[Sample(input="Authored native compaction QA", id="fixture")],
        solver=private_compaction(),
        model=get_model("checkpoint_compaction_fixture/model"),
        config=GenerateConfig(max_tokens=128000, max_retries=300, attempt_timeout=900),
        checkpoint=CheckpointConfig(
            trigger=Manual(),
            sandbox_paths={},
            retention="retain",
            max_consecutive_failures=0,
        ),
        fail_on_error=True,
        metadata={"development_only": True, "actual_private_compaction": True},
    )
