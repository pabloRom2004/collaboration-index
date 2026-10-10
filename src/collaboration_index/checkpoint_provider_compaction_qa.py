"""Probe forced native compaction with an explicitly supplied real Hawk model."""

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
    compaction,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import CheckpointConfig, Manual, checkpointer

from collaboration_index.checkpoints import PeerCheckpointer

PROCESS = uuid.uuid4().hex


@solver
def provider_private_compaction() -> Solver:
    """Own one native checkpoint with two independent actual compaction states."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        """Save compacted message identities then verify them after fresh-process hydration."""
        async with checkpointer() as cp:
            proof = cast(
                dict[str, Any],
                cp.track("proof", lambda: proof, {}, value_type=dict[str, Any]),
            )
            if cp.attempt != "initial":
                assert state.token_usage == proof["token_usage"]
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
                    CompactionAuto(threshold=787500),
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
                proof["token_usage"] = state.token_usage
                state.store.set(
                    "actual_compaction_baseline",
                    {
                        "private_peers": 2,
                        "compacted_hashes": {
                            actor: proof[actor]["sha256"] for actor in ["one", "two"]
                        },
                        "summary_calls": 2,
                        "cumulative_actual_tokens": state.token_usage,
                        "normal_compaction_threshold": 787500,
                        "effective_context": 1050000,
                        "forced_below_normal_threshold": True,
                    },
                )
                await cp.checkpoint()
                state.store.set("fresh_compaction_injected_fault", True)
                raise RuntimeError("Authored actual-compaction checkpoint failure")
            assert state.token_usage == proof["token_usage"]
            state.store.set(
                "fresh_compaction_proof",
                {
                    "peers": 2,
                    "exact_compacted_ids": True,
                    "exact_compacted_hashes": True,
                    "fresh_process": True,
                    "usage_restored": True,
                    "cumulative_actual_tokens": state.token_usage,
                },
            )
        return state

    return solve


@task
def checkpoint_provider_compaction_qa() -> Task:
    """Configure sandbox-free authored native compaction with retained checkpoint storage."""
    return Task(
        dataset=[Sample(input="Authored native compaction QA", id="fixture")],
        solver=provider_private_compaction(),
        config=GenerateConfig(max_tokens=128000, max_retries=300, attempt_timeout=900),
        checkpoint=CheckpointConfig(
            trigger=Manual(),
            sandbox_paths={},
            retention="retain",
            max_consecutive_failures=0,
        ),
        fail_on_error=True,
        metadata={
            "development_only": True,
            "actual_private_compaction": True,
            "forced_compaction_qa": True,
        },
    )
