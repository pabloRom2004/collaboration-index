"""Dedicated provider QA for Inspect's native agent-complete scoring-only resume branch."""

import hashlib
from typing import Any

from inspect_ai import Task, task
from inspect_ai.scorer import Score, Scorer, Target, scorer
from inspect_ai.solver import Generate, Solver, TaskState, chain, solver
from inspect_ai.util import checkpointer, sandbox

from collaboration_index.checkpoint_finished_qa import PROCESS, checkpoint_finished_qa
from collaboration_index.checkpoint_qa import digest
from collaboration_index.checkpoints import TeamCheckpoints

CAPTURED_OWNER: Any = None
ORIGINAL_INIT = getattr(
    TeamCheckpoints, "_scoring_qa_original_init", TeamCheckpoints.__init__
)


def capture_owner(self: Any, *args: Any, **kwargs: Any) -> None:
    """Retain the dedicated QA owner's already-registered native private snapshot."""
    global CAPTURED_OWNER
    ORIGINAL_INIT(self, *args, **kwargs)
    CAPTURED_OWNER = self


async def scoring_save(self: Any, hold: bool = False) -> None:
    """Commit finished-state evidence without injecting a pre-agent-complete failure."""
    from collaboration_index.checkpoint_finished_qa import finished_save

    try:
        await finished_save(self, hold)
    except RuntimeError as error:
        if (
            str(error)
            != "Authored failure after both native-limited peers were durably saved"
        ):
            raise
        self.task.store.set("finished_scoring_injected_failure", False)


@task
def checkpoint_scoring_qa(artifact_dir: str = "/tmp/checkpoint-scoring-qa") -> Task:
    """Fail the scorer after native agent-complete, then verify a genuine scoring-only resume."""
    result = checkpoint_finished_qa(artifact_dir=artifact_dir)
    setattr(TeamCheckpoints, "save", scoring_save)
    setattr(TeamCheckpoints, "_scoring_qa_original_init", ORIGINAL_INIT)
    setattr(TeamCheckpoints, "__init__", capture_owner)
    original_solver = (
        chain(*result.solver) if isinstance(result.solver, list) else result.solver
    )
    original_scorer = (
        result.scorer[0] if isinstance(result.scorer, list) else result.scorer
    )
    assert callable(original_solver) and callable(original_scorer)

    @solver
    def branch_probe() -> Solver:
        """Record the native attempt branch after the production solver returns."""

        async def solve(state: TaskState, generate: Generate) -> TaskState:
            """Require exact private snapshots and files on native scoring-only restoration."""
            state = await original_solver(state, generate)
            async with checkpointer() as cp:
                state.store.set("scoring_qa_attempt", cp.attempt)
                if cp.attempt != "initial":
                    assert cp.attempt == "resume_for_scoring"
                    assert CAPTURED_OWNER is not None
                    snapshot = CAPTURED_OWNER.snapshot
                    prior = state.store.get("finished_scoring_saved")
                    assert prior["process"] != PROCESS
                    assert (
                        prior["hostname"]
                        != (await sandbox().exec(["hostname"])).stdout.strip()
                    )
                    assert prior["private_hashes"] == {
                        a: digest([m.model_dump(mode="json") for m in messages])
                        for a, messages in snapshot.messages.items()
                    }
                    assert prior["output_hashes"] == {
                        a: digest(o.model_dump(mode="json"))
                        for a, o in snapshot.outputs.items()
                    }
                    assert prior["usage"] == dict(snapshot.usages)
                    assert prior["logical_tokens"] == state.token_usage
                    assert snapshot.board is not None
                    assert prior["board_hash"] == snapshot.board["sha256"]
                    assert (
                        prior["file_hash"]
                        == hashlib.sha256(
                            (
                                await sandbox().read_file(
                                    "/workdir/checkpoint-qa-marker"
                                )
                            ).encode()
                        ).hexdigest()
                    )
                    state.store.set("scoring_qa_exact_restored", True)
            return state

        return solve

    @scorer(metrics=[])
    def fail_first_score() -> Scorer:
        """Inject the initial scoring failure and delegate resumed authoritative Ruff grading."""

        async def score(state: TaskState, target: Target) -> Score | None:
            """Require native scoring-only restore before completing the real upstream scorer."""
            if state.store.get("scoring_qa_attempt") == "initial":
                state.store.set("scoring_qa_intended_failure", True)
                raise RuntimeError(
                    "Authored scorer failure after native agent_complete checkpoint"
                )
            assert state.store.get("scoring_qa_attempt") == "resume_for_scoring"
            assert state.store.get("scoring_qa_exact_restored") is True
            return await original_scorer(state, target)

        return score

    result.solver = branch_probe()
    result.scorer = [fail_first_score()]
    return result
