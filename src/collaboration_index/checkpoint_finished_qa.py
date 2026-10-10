"""Dedicated actual-provider QA for native finished peers and scorer-only continuation."""

import hashlib
import uuid
from typing import Any

from inspect_ai import Task, task
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

from collaboration_index.checkpoint_qa import ORIGINAL_SAVE, checkpoint_ruff_qa, digest
from collaboration_index.checkpoints import TeamCheckpoints

PROCESS = uuid.uuid4().hex


async def finished_save(self: Any, hold: bool = False) -> None:
    """Delegate native save and fail only after all trusted peer statuses are limited."""
    native = self.cp.checkpoint
    restored_board_hash = self.snapshot.board["sha256"] if self.snapshot.board else None

    async def commit() -> None:
        """Verify fresh restored finished state before committing a scorer-only checkpoint."""
        prior = self.task.store.get("finished_scoring_saved")
        complete = all(p.status == "limited" for p in self.history.peers)
        facts = {
            "process": PROCESS,
            "hostname": (await sandbox().exec(["hostname"])).stdout.strip(),
            "private_hashes": {
                a: digest([m.model_dump(mode="json") for m in messages])
                for a, messages in self.snapshot.messages.items()
            },
            "output_hashes": {
                a: digest(o.model_dump(mode="json"))
                for a, o in self.snapshot.outputs.items()
            },
            "usage": dict(self.snapshot.usages),
            "logical_tokens": self.task.token_usage,
            "board_hash": restored_board_hash
            if self.cp.attempt != "initial"
            else self.snapshot.board["sha256"],
            "file_hash": hashlib.sha256(
                (await sandbox().read_file("/workdir/checkpoint-qa-marker")).encode()
            ).hexdigest(),
            "calls": self.task.store.get("checkpoint_qa_calls", {}),
            "all_native_limited": complete,
        }
        if self.cp.attempt != "initial":
            assert (
                complete
                and prior["all_native_limited"]
                and prior["process"] != PROCESS
                and prior["hostname"] != facts["hostname"]
            )
            assert all(
                prior[k] == facts[k]
                for k in [
                    "private_hashes",
                    "output_hashes",
                    "usage",
                    "logical_tokens",
                    "board_hash",
                    "file_hash",
                    "calls",
                ]
            )
            self.task.store.set(
                "finished_scoring_restored",
                {
                    "fresh_process": True,
                    "fresh_sandbox": True,
                    "exact_private_outputs_usage_files_board": True,
                    "all_native_limited": True,
                    "no_additional_model_calls": True,
                    "logical_tokens": facts["logical_tokens"],
                },
            )
        else:
            self.task.store.set("finished_scoring_saved", facts)
        await native()

    self.cp.checkpoint = commit
    try:
        await ORIGINAL_SAVE(self, hold)
    finally:
        self.cp.checkpoint = native
    if self.cp.attempt == "initial" and all(
        p.status == "limited" for p in self.history.peers
    ):
        self.task.store.set("finished_scoring_injected_failure", True)
        raise RuntimeError(
            "Authored failure after both native-limited peers were durably saved"
        )


@task
def checkpoint_finished_qa(artifact_dir: str = "/tmp/checkpoint-finished-qa") -> Task:
    """Use a supplied real provider to exhaust two native caps before scorer-only restoration."""
    result = checkpoint_ruff_qa(
        mode="provider", artifact_dir=artifact_dir, token_limit_per_agent=1, agents=2
    )
    setattr(TeamCheckpoints, "save", finished_save)

    @solver
    def stamp_files() -> Solver:
        """Stamp a different authored file in each fresh process before archive restoration."""

        async def solve(state: TaskState, generate: Generate) -> TaskState:
            """Require archive restoration to recover the previous controller's marker."""
            await sandbox().write_file(
                "/workdir/checkpoint-qa-marker",
                "Authored finished QA marker " + PROCESS,
            )
            return state

        return solve

    assert result.setup is not None
    result.setup = (
        [*result.setup, stamp_files()]
        if isinstance(result.setup, list)
        else [result.setup, stamp_files()]
    )
    return result
