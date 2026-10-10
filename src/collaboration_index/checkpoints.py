"""Public-API primitives for a quiescent multi-peer checkpoint owner."""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from inspect_ai.agent import Agent, AgentState
from inspect_ai.model import (
    ChatMessage,
    ChatMessageUser,
    CompactionAuto,
    ModelOutput,
    ModelRefusalError,
    compaction,
    get_model,
)
from inspect_ai.solver import TaskState
from inspect_ai.util import Checkpointer, ResumeReport, sandbox
from pydantic import BaseModel, Field

from collaboration_index.board.checkpoint import snapshot_board
from collaboration_index.board.client import LIMITS
from collaboration_index.state import TeamHistory

T = TypeVar("T")


class PeerCheckpointer:
    """Namespace tracked peer state without owning the sample checkpoint lifecycle."""

    def __init__(self, owner: Checkpointer, peer: str) -> None:
        """Bind one fixed evaluator peer to the sample owner."""
        self.owner, self.peer = owner, peer

    @property
    def attempt(self) -> Literal["initial", "resume", "resume_for_scoring"]:
        """Expose the owner's restore mode."""
        return self.owner.attempt

    @property
    def restored(self) -> ResumeReport | None:
        """Expose the owner's non-participant restore report."""
        return self.owner.restored

    def track(
        self,
        key: str,
        callback: Callable[[], T],
        initial_value: T,
        *,
        value_type: type[T] | None = None,
    ) -> T:
        """Register a peer field under its fixed namespace."""
        return self.owner.track(
            f"peer:{self.peer}:{key}", callback, initial_value, value_type=value_type
        )

    async def tick(self) -> None:
        """Leave snapshot scheduling exclusively to the team coordinator."""

    async def checkpoint(self) -> None:
        """Reject peer-owned snapshots that could capture another peer mid-turn."""
        raise RuntimeError("Only the team coordinator can commit a checkpoint")

    def span_session(self) -> AbstractAsyncContextManager[None]:
        """Keep transcript ownership with the sample owner."""
        return contextlib.nullcontext()


class TeamBarrier:
    """Stop every live peer at a completed turn before committing shared state."""

    def __init__(
        self, peers: set[str], interval: float, save: Callable[[], Awaitable[None]]
    ) -> None:
        """Create one coordinator with explicit fixed peers and snapshot callback."""
        if not peers or interval <= 0:
            raise ValueError("A checkpoint barrier needs peers and a positive interval")
        self.live = set(peers)
        self.waiting: set[str] = set()
        self.interval, self.save = interval, save
        self.last = time.monotonic()
        self.condition = asyncio.Condition()
        self.requested = False
        self.failure: BaseException | None = None
        self.generation = 0

    async def boundary(self, peer: str) -> None:
        """Wait at a completed turn until all live peers can save together."""
        async with self.condition:
            if self.failure is not None:
                raise RuntimeError("Team checkpoint aborted") from self.failure
            if peer not in self.live:
                raise ValueError("Unknown or finished checkpoint peer")
            if time.monotonic() - self.last >= self.interval:
                self.requested = True
            if not self.requested:
                return
            generation = self.generation
            self.waiting.add(peer)
            await self._commit_if_ready()
            await self.condition.wait_for(
                lambda: self.generation != generation or self.failure is not None
            )
            if self.failure is not None:
                raise RuntimeError("Team checkpoint aborted") from self.failure

    async def finished(self, peer: str) -> None:
        """Remove a finished peer without stranding peers already waiting to save."""
        async with self.condition:
            if peer not in self.live:
                raise ValueError("Unknown or already finished checkpoint peer")
            self.live.remove(peer)
            self.waiting.discard(peer)
            await self._commit_if_ready()

    async def abort(self, failure: BaseException) -> None:
        """Release every waiter while retaining the first peer or infrastructure failure."""
        async with self.condition:
            if self.failure is None:
                self.failure = failure
            self.condition.notify_all()

    async def _commit_if_ready(self) -> None:
        """Commit once while no live peer can leave its safe boundary."""
        if not self.requested or self.waiting != self.live:
            return
        try:
            await self.save()
        except BaseException as error:
            self.failure = error
            self.condition.notify_all()
            raise
        self.last = time.monotonic()
        self.waiting.clear()
        self.requested = False
        self.generation += 1
        self.condition.notify_all()


class TeamSnapshot(BaseModel):
    """Serialize private peer conversations and controller-only board state together."""

    run_id: str = ""
    board: dict[str, str | int] | None = None
    messages: dict[str, list[ChatMessage]] = Field(default_factory=dict)
    prefixes: dict[str, list[ChatMessage]] = Field(default_factory=dict)
    outputs: dict[str, ModelOutput] = Field(default_factory=dict)
    usages: dict[str, int] = Field(default_factory=dict)


# Peer tasks inherit the owner without sharing it across samples.
OWNER: ContextVar[Any] = ContextVar("team_checkpoint_owner", default=None)


class TeamCheckpoints:
    """Own a native sample checkpoint while coordinating every private peer safely."""

    def __init__(
        self,
        cp: Checkpointer,
        state: TaskState,
        interval: float,
        threshold: float | int,
    ) -> None:
        """Restore stable team state and create one all-peer checkpoint barrier."""
        self.cp, self.task = cp, state
        from inspect_ai.util._checkpoint.checkpointer_noop import _NoopCheckpointer

        if isinstance(cp, _NoopCheckpointer):
            raise RuntimeError(
                "Team checkpointing was enabled but native checkpoint storage is disabled"
            )
        self.snapshot = cp.track("team_snapshot", lambda: self.snapshot, TeamSnapshot())
        self.history = state.store_as(TeamHistory)
        if self.snapshot.run_id and self.snapshot.run_id != self.history.run_id:
            raise ValueError(
                "Checkpoint team identity differs from restored scoring state"
            )
        self.snapshot.run_id = self.history.run_id
        actors = {peer.id for peer in self.history.peers}
        if cp.attempt != "initial" and (
            self.snapshot.board is None
            or set(self.snapshot.messages) != actors
            or set(self.snapshot.outputs) != actors
            or set(self.snapshot.prefixes) != actors
            or set(self.snapshot.usages) != actors
            or any(usage < 0 for usage in self.snapshot.usages.values())
        ):
            raise ValueError(
                "Checkpoint does not contain every private peer and its usage"
            )
        from collaboration_index.checkpoint_backup import install_host_backup

        install_host_backup(cp)
        state.metadata["checkpoint_host_backup"] = {
            "restic_version": "0.19.1",
            "frozen_restore_exports": True,
            "adapter_inspect_version": "0.3.277",
            "cache_enabled": False,
        }
        self.current: dict[str, AgentState] = {}
        self.meters: dict[str, Any] = {}
        self.prior = dict(self.snapshot.usages)
        self.frozen: dict[str, str] | None = None
        self.finished_compactions: dict[str, Any] = {}
        for peer in self.history.peers:
            if cp.attempt != "initial" and peer.status in {"limited", "completed"}:
                self.retain_finished(peer.id, threshold)
        self.barrier = TeamBarrier(
            {peer.id for peer in self.history.peers}, interval, self.save
        )

    def retain_finished(self, actor: str, threshold: float | int) -> None:
        """Keep a skipped peer's native compaction in every later checkpoint without model work."""
        if actor not in self.finished_compactions:
            self.finished_compactions[actor] = compaction(
                CompactionAuto(threshold=threshold),
                prefix=self.snapshot.prefixes[actor],
                checkpointer=PeerCheckpointer(self.cp, actor),
            )

    def generation(
        self, actor: str, threshold: float | int, retry_refusals: int | None
    ) -> Agent:
        """Build a trusted native generator with independently resumable compaction state."""
        scoped = PeerCheckpointer(self.cp, actor)
        compact = None
        initialized = False

        async def generate(current: AgentState, tools: list[Any]) -> AgentState:
            """Restore private history once and generate only after the shared checkpoint boundary."""
            nonlocal compact, initialized
            if not initialized:
                if actor not in self.snapshot.prefixes:
                    self.snapshot.prefixes[actor] = [
                        message.model_copy(deep=True) for message in current.messages
                    ]
                prefix = [
                    message.model_copy(deep=True)
                    for message in self.snapshot.prefixes[actor]
                ]
                if actor in self.snapshot.messages:
                    current.messages = [
                        message.model_copy(deep=True)
                        for message in self.snapshot.messages[actor]
                    ]
                    current.output = self.snapshot.outputs[actor].model_copy(deep=True)
                    current.messages.append(
                        ChatMessageUser(
                            content="The team was restored from a durable checkpoint into a fresh sandbox. Your files, message board and private conversation were restored, but background processes and uncaptured paths were not. Restart any background processes you still need; continue within your original token allowance."
                        )
                    )
                compact = compaction(
                    CompactionAuto(threshold=threshold),
                    prefix=prefix,
                    tools=tools,
                    checkpointer=scoped,
                )
                initialized = True
            self.current[actor] = current
            await self.barrier.boundary(actor)
            assert compact is not None
            inputs, supplemental = await compact.compact_input(current.messages)
            if supplemental is not None:
                current.messages.append(supplemental)
            attempts = 0
            while True:
                try:
                    output = await get_model().generate(inputs, tools)
                except ModelRefusalError:
                    if retry_refusals is not None and attempts < retry_refusals:
                        attempts += 1
                        continue
                    raise
                if (
                    not output.empty
                    and output.stop_reason == "content_filter"
                    and retry_refusals is not None
                    and attempts < retry_refusals
                ):
                    attempts += 1
                    continue
                current.output = output
                current.messages.append(output.message)
                await compact.record_output(inputs, output)
                return current

        return cast(Agent, generate)

    async def save(self, hold: bool = False) -> None:
        """Freeze participant filesystem writers and commit the board and all peer histories."""
        import json

        import httpx

        receipt = json.loads(
            (Path(self.history.artifact_dir) / "board-receipt.json").read_text()
        )
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            async with asyncio.timeout(60):
                while True:
                    response = await client.get(receipt["url"] + "/health/checkpoint")
                    response.raise_for_status()
                    if response.json()["active_requests"] == 0:
                        break
                    await asyncio.sleep(0.05)

        frozen = await sandbox().exec(["python3", "-c", FREEZE_PROCESSES])
        if not frozen.success:
            raise RuntimeError(
                "Cannot freeze sandbox writers for a consistent checkpoint"
            )
        identities = json.loads(frozen.stdout)
        self.frozen = identities
        try:
            for peer in self.history.peers:
                if peer.id in self.current:
                    current = self.current[peer.id]
                    self.snapshot.messages[peer.id] = [
                        message.model_copy(deep=True) for message in current.messages
                    ]
                    self.snapshot.outputs[peer.id] = current.output.model_copy(
                        deep=True
                    )
                if peer.id in self.meters:
                    used = self.prior.get(peer.id, 0) + int(self.meters[peer.id].usage)
                    self.snapshot.usages[peer.id] = used
                    peer.tokens = used
            self.snapshot.board = snapshot_board(
                Path(self.history.artifact_dir) / "board.sqlite",
                LIMITS["storage_bytes"],
            )
            await self.cp.checkpoint()
            self.task.store.set(
                "team_checkpoint_commits",
                self.task.store.get("team_checkpoint_commits", 0) + 1,
            )
        finally:
            if not hold:
                await self.thaw()

    async def thaw(self) -> None:
        """Resume only the exact processes stopped by this sample checkpoint owner."""
        import json

        if self.frozen is None:
            return
        identities = self.frozen
        thawed = await sandbox().exec(
            ["python3", "-c", THAW_PROCESSES, json.dumps(identities)]
        )
        if not thawed.success:
            raise RuntimeError("Cannot resume sandbox writers after checkpoint")
        self.frozen = None


FREEZE_PROCESSES = r"""
import json, os, signal, time
from pathlib import Path
excluded = {1, os.getpid()}
pid = os.getppid()
while pid > 1:
    excluded.add(pid)
    pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
frozen = {}
try:
    for _ in range(100):
        changed = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) in excluded:
                continue
            pid = int(entry.name)
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if fields[0] in {"Z", "T", "t"} or pid in frozen:
                    continue
                started = fields[19]
                os.kill(pid, signal.SIGSTOP)
                frozen[pid] = started
                for attempt in range(100):
                    state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
                    if state in {"T", "t", "Z"}:
                        break
                    time.sleep(.01)
                else:
                    raise RuntimeError("Sandbox writer did not stop")
                changed = True
            except ProcessLookupError:
                continue
            except FileNotFoundError:
                continue
        if not changed:
            break
    else:
        raise RuntimeError("Sandbox processes did not quiesce")
    print(json.dumps(frozen))
except BaseException:
    for pid, started in frozen.items():
        try:
            actual = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            if actual == started:
                os.kill(pid, signal.SIGCONT)
        except (ProcessLookupError, FileNotFoundError):
            pass
    raise
"""

THAW_PROCESSES = r"""
import json, os, signal, sys
from pathlib import Path
for value, started in json.loads(sys.argv[1]).items():
    pid = int(value)
    try:
        actual = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        if actual == started:
            os.kill(pid, signal.SIGCONT)
    except (ProcessLookupError, FileNotFoundError):
        pass
"""
