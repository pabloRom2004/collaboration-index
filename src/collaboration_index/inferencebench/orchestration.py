"""Share GPU leases, account for GPU use and queue graded submissions for one InferenceBench team."""

from __future__ import annotations

import asyncio
import math
import secrets
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

# fraction of a GPU's memory in use at which a memory-pressure event is recorded
MEMORY_PRESSURE = 0.97


@dataclass
class Span:
    """Record when one lease held one GPU."""

    slot: int
    start: float
    end: float | None = None


@dataclass
class Lease:
    """Record one agent's exclusive GPUs, what they are for and how the grant ended."""

    token: str
    actor: str
    slots: list[int]
    requested_at: float
    granted_at: float
    expires_at: float
    note: str = ""
    ended_at: float | None = None
    end_reason: str | None = None
    spans: list[Span] = field(default_factory=list)
    reclaimed: int = 0
    peak: int = 0


@dataclass
class _Waiter:
    """Hold one agent's queued request for a first GPU, shared by its concurrent calls."""

    actor: str
    requested_at: float
    count: int
    note: str
    future: asyncio.Future[Lease]
    callers: int = 1


class GpuScheduler:
    """Share a pool of GPU slots among agents fairly while keeping every slot in use.

    Each agent holds at most one lease, which can cover several GPUs. Agents
    without a GPU are served first, in arrival order. A lease gets extra GPUs
    only while nobody waits, and extra GPUs are the first ones taken back when
    someone does. A lease's last GPU is taken back only after ``lease_seconds``
    and only while another agent waits. No GPU is taken from a protected lease.
    """

    def __init__(
        self, slots: int, lease_seconds: float, clock: Callable[[], float] = time.time
    ) -> None:
        """Start with every slot free."""
        self.slots = slots
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.started = clock()
        self.holders: dict[int, Lease] = {}
        self.leases: list[Lease] = []
        self.waiting: list[_Waiter] = []
        self.timeouts: list[dict[str, Any]] = []
        # processes to stop before a slot is used again: every process of an
        # ended lease, and the processes a shrunk lease started for that slot
        self.stale: dict[int, set[str]] = {}
        self.shrunk: dict[int, set[str]] = {}
        self.busy: dict[str, int] = {}

    def held(self, actor: str) -> Lease | None:
        """Return the actor's active lease, if any."""
        return next((h for h in self.holders.values() if h.actor == actor), None)

    def free(self) -> list[int]:
        """List unassigned slots in index order."""
        return [slot for slot in range(self.slots) if slot not in self.holders]

    def _add(self, lease: Lease, slot: int) -> None:
        """Assign one free slot to a lease."""
        self.holders[slot] = lease
        lease.slots.append(slot)
        lease.spans.append(Span(slot, self.clock()))
        lease.peak = max(lease.peak, len(lease.slots))
        # a lease that gets a GPU back owns its earlier processes there again
        self.shrunk.get(slot, set()).discard(lease.token)

    def _drop(self, lease: Lease, slot: int) -> None:
        """Take one slot from a lease and close its span."""
        del self.holders[slot]
        lease.slots.remove(slot)
        for span in lease.spans:
            if span.slot == slot and span.end is None:
                span.end = self.clock()

    def _shrink(self, lease: Lease, slot: int) -> None:
        """Take one GPU from a lease that keeps others and mark that GPU's processes for cleanup."""
        self._drop(lease, slot)
        self.shrunk.setdefault(slot, set()).add(lease.token)

    def _close(self, lease: Lease, reason: str) -> None:
        """End a lease and mark all of its processes for cleanup."""
        for slot in list(lease.slots):
            self._drop(lease, slot)
            self.stale.setdefault(slot, set()).add(lease.token)
        lease.ended_at, lease.end_reason = self.clock(), reason

    def _rebalance(self) -> None:
        """Free a slot for every waiting agent where possible: extra GPUs first, then expired leases."""
        while len(self.waiting) > len(self.free()):
            idle = [h for h in self.holders.values() if not self.busy.get(h.token)]
            extras = [h for h in idle if len(h.slots) > 1]
            if extras:
                # the newest extra GPU of the agent holding the most
                lease = max(extras, key=lambda h: (len(h.slots), -h.granted_at))
                self._shrink(lease, lease.slots[-1])
                lease.reclaimed += 1
                continue
            now = self.clock()
            expired = [h for h in idle if now >= h.expires_at]
            if not expired:
                return
            self._close(min(expired, key=lambda h: h.granted_at), "expired")

    def _dispatch(self) -> None:
        """Give every waiting agent one GPU in arrival order, then extras if nobody waits."""
        free = self.free()
        granted = []
        for waiter in list(self.waiting):
            if not free:
                break
            now = self.clock()
            lease = Lease(
                token=secrets.token_hex(12),
                actor=waiter.actor,
                slots=[],
                requested_at=waiter.requested_at,
                granted_at=now,
                expires_at=now + self.lease_seconds,
                note=waiter.note,
            )
            self._add(lease, free.pop(0))
            self.leases.append(lease)
            self.waiting.remove(waiter)
            granted.append((waiter, lease))
        for waiter, lease in granted:
            while free and not self.waiting and len(lease.slots) < waiter.count:
                self._add(lease, free.pop(0))
            waiter.future.set_result(lease)

    def tick(self) -> None:
        """Rebalance leases and grant GPUs to waiting agents."""
        self._rebalance()
        self._dispatch()

    async def acquire(
        self, actor: str, wait: float, count: int = 1, note: str = ""
    ) -> Lease | None:
        """Get up to ``count`` GPUs, waiting up to ``wait`` seconds for a first one."""
        self.tick()
        lease = self.held(actor)
        if lease is not None:
            # a holder grows its lease with free GPUs while nobody waits
            if note:
                lease.note = note
            free = self.free()
            while free and not self.waiting and len(lease.slots) < count:
                self._add(lease, free.pop(0))
            return lease
        # concurrent calls by one agent share its single place in the queue
        waiter = next((w for w in self.waiting if w.actor == actor), None)
        if waiter is None:
            waiter = _Waiter(
                actor,
                self.clock(),
                count,
                note,
                asyncio.get_running_loop().create_future(),
            )
            self.waiting.append(waiter)
            self.tick()
        else:
            waiter.callers += 1
            waiter.count = max(waiter.count, count)
            waiter.note = note or waiter.note
        try:
            await asyncio.wait({waiter.future}, timeout=wait)
        except BaseException:
            self._leave(waiter)
            raise
        if waiter.future.cancelled():
            # finish() withdrew this request because the peer's run ended
            return None
        if waiter.future.done():
            return waiter.future.result()
        if self._leave(waiter):
            self.timeouts.append(
                {
                    "actor": actor,
                    "requested_at": waiter.requested_at,
                    "gave_up_at": self.clock(),
                }
            )
        return None

    def _leave(self, waiter: _Waiter) -> bool:
        """Drop one caller from a request and withdraw it when no caller remains."""
        waiter.callers -= 1
        if waiter.callers:
            return False
        if waiter.future.done() and not waiter.future.cancelled():
            # release a grant that completed while the last caller was cancelled
            self.release(waiter.actor, reason="cancelled")
        else:
            if waiter in self.waiting:
                self.waiting.remove(waiter)
            self.tick()
        return True

    def release(
        self, actor: str, slot: int | None = None, reason: str = "released"
    ) -> list[int]:
        """Give back one GPU of a multi-GPU lease, or the whole lease, and return the freed slots."""
        lease = self.held(actor)
        if lease is None or (slot is not None and slot not in lease.slots):
            return []
        if slot is not None and len(lease.slots) > 1:
            self._shrink(lease, slot)
            freed = [slot]
        else:
            freed = list(lease.slots)
            self._close(lease, reason)
        self.tick()
        return freed

    def finish(self, actor: str) -> list[int]:
        """Release the lease and queued request of a peer whose run has ended."""
        for waiter in [w for w in self.waiting if w.actor == actor]:
            self.waiting.remove(waiter)
            waiter.future.cancel()
        return self.release(actor, reason="finished")

    def protect(self, lease: Lease) -> bool:
        """Keep every GPU of an active lease until the matching unprotect call."""
        if lease.ended_at is not None:
            return False
        self.busy[lease.token] = self.busy.get(lease.token, 0) + 1
        return True

    def unprotect(self, lease: Lease) -> None:
        """End one protection and apply any rebalancing it deferred."""
        count = self.busy.get(lease.token, 0) - 1
        if count > 0:
            self.busy[lease.token] = count
        else:
            self.busy.pop(lease.token, None)
        self.tick()

    def has_cleanup(self, slot: int) -> bool:
        """Report whether processes of earlier holders are still to be stopped on a slot."""
        return bool(self.stale.get(slot) or self.shrunk.get(slot))

    def take_cleanup(self, slot: int) -> tuple[set[str], set[str]]:
        """Return and forget a slot's cleanup work: ended-lease tokens and shrunk-lease tokens."""
        return self.stale.pop(slot, set()), self.shrunk.pop(slot, set())

    def restore_cleanup(self, slot: int, stale: set[str], shrunk: set[str]) -> None:
        """Put back cleanup work that failed, except for a lease that holds the slot again."""
        holder = self.holders.get(slot)
        shrunk = shrunk - {holder.token} if holder else shrunk
        if stale:
            self.stale.setdefault(slot, set()).update(stale)
        if shrunk:
            self.shrunk.setdefault(slot, set()).update(shrunk)

    def status(self, actor: str, names: dict[str, str]) -> dict[str, Any]:
        """Describe every GPU to one peer by teammates' board names, never evaluator IDs."""
        self.tick()
        now = self.clock()
        lease = self.held(actor)
        position = next(
            (i + 1 for i, w in enumerate(self.waiting) if w.actor == actor), None
        )
        gpus: list[dict[str, Any]] = []
        for slot in range(self.slots):
            holder = self.holders.get(slot)
            if holder is None:
                gpus.append({"gpu": slot, "holder": None})
                continue
            gpus.append(
                {
                    "gpu": slot,
                    "holder": "you"
                    if holder.actor == actor
                    else names.get(holder.actor, "an unregistered teammate"),
                    "note": holder.note,
                    "held_seconds": round(now - holder.granted_at),
                }
            )
        return {
            "gpus": gpus,
            "free_gpus": len(self.free()),
            "waiting_agents": len(self.waiting),
            "your_queue_position": position,
            "your_gpus": sorted(lease.slots) if lease else [],
            "your_lease_reclaimable_in_seconds": max(0, round(lease.expires_at - now))
            if lease
            else None,
            "lease_seconds": self.lease_seconds,
        }

    def summary(
        self, start: float | None = None, end: float | None = None
    ) -> dict[str, Any]:
        """Summarize waits, GPU holding time, reclamations and slot occupancy over an interval."""
        start = self.started if start is None else start
        end = self.clock() if end is None else end
        window = max(end - start, 1e-9)
        waits = [lease.granted_at - lease.requested_at for lease in self.leases]
        # holding time inside the interval, for spans that overlap it
        overlaps = [
            min(end, end if span.end is None else span.end) - max(start, span.start)
            for lease in self.leases
            for span in lease.spans
        ]
        held = [seconds for seconds in overlaps if seconds > 0]
        reasons: dict[str, int] = {}
        for lease in self.leases:
            key = lease.end_reason or "active"
            reasons[key] = reasons.get(key, 0) + 1
        return {
            "slots": self.slots,
            "grants": len(self.leases),
            "timeouts": len(self.timeouts),
            "end_reasons": reasons,
            "extra_gpus_reclaimed": sum(lease.reclaimed for lease in self.leases),
            "max_gpus_per_lease": max((lease.peak for lease in self.leases), default=0),
            "wait_seconds": describe(waits),
            "gpu_hold_seconds": describe(held),
            "occupancy": sum(held) / (self.slots * window),
            "leases": [asdict(lease) for lease in self.leases],
        }


def describe(values: list[float]) -> dict[str, float | int | None]:
    """Return count, mean, median, 90th percentile and maximum of a sample."""
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": statistics.fmean(ordered),
        "p50": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)],
        "max": ordered[-1],
    }


class GpuUsage:
    """Fold GPU probes into time-weighted utilization, memory, power and lease coverage."""

    FIELDS = [
        "seconds",
        "busy",
        "leased",
        "leased_busy",
        "util",
        "util_seconds",
        "power",
        "power_seconds",
        "peak_memory_mib",
    ]

    def __init__(self, interval: float) -> None:
        """Start empty totals; ``interval`` weighs a probe whose duration was not measured."""
        self.interval = interval
        self.samples = self.violations = self.errors = 0
        self.gpus: dict[int, dict[str, float]] = {}
        self.pressured: set[int] = set()

    def add(
        self,
        sample: dict[str, Any],
        holders: dict[int, str],
        violations: int,
        seconds: float | None,
    ) -> list[dict[str, Any]]:
        """Weight one probe by the time it represents and return new memory-pressure events."""
        seconds = self.interval if seconds is None else seconds
        self.samples += 1
        self.violations += violations
        events = []
        for gpu in sample["gpus"]:
            index = gpu["index"]
            stats = self.gpus.setdefault(index, dict.fromkeys(self.FIELDS, 0.0))
            util, power = gpu["utilization_percent"], gpu["power_watts"]
            used, total = gpu["memory_used_mib"], gpu["memory_total_mib"]
            busy = util is not None and util >= 1
            stats["seconds"] += seconds
            stats["leased"] += seconds * (index in holders)
            stats["busy"] += seconds * busy
            stats["leased_busy"] += seconds * (busy and index in holders)
            if util is not None:
                stats["util"] += util * seconds
                stats["util_seconds"] += seconds
            if power is not None:
                stats["power"] += power * seconds
                stats["power_seconds"] += seconds
            if used is not None:
                stats["peak_memory_mib"] = max(stats["peak_memory_mib"], used)
                if total and used / total >= MEMORY_PRESSURE:
                    if index not in self.pressured:
                        events.append(
                            {
                                "gpu": index,
                                "memory_used_mib": used,
                                "memory_total_mib": total,
                            }
                        )
                    self.pressured.add(index)
                else:
                    self.pressured.discard(index)
        return events

    def summary(self) -> dict[str, Any]:
        """Report per-GPU time-weighted means and team totals in GPU-hours and watt-hours."""

        def ratio(top: float, bottom: float) -> float | None:
            """Divide, returning None for an empty denominator."""
            return top / bottom if bottom else None

        gpus = {
            index: {
                "mean_utilization_percent": ratio(s["util"], s["util_seconds"]),
                "busy_fraction": ratio(s["busy"], s["seconds"]),
                "leased_fraction": ratio(s["leased"], s["seconds"]),
                "busy_while_leased_fraction": ratio(s["leased_busy"], s["leased"]),
                "peak_memory_mib": s["peak_memory_mib"],
                "mean_power_watts": ratio(s["power"], s["power_seconds"]),
            }
            for index, s in sorted(self.gpus.items())
        }
        stats = self.gpus.values()
        return {
            "samples": self.samples,
            "probe_errors": self.errors,
            "interval_seconds": self.interval,
            "unleased_gpu_process_observations": self.violations,
            "observed_gpu_hours": sum(s["seconds"] for s in stats) / 3600,
            "busy_gpu_hours": sum(s["busy"] for s in stats) / 3600,
            "energy_watt_hours": sum(s["power"] for s in stats) / 3600
            if any(s["power_seconds"] for s in stats)
            else None,
            "gpus": gpus,
        }


class QueueFull(Exception):
    """Signal that a submission was refused because the grading queue is full."""


@dataclass
class Submission:
    """Track one team submission from snapshot to its hidden-evaluation result."""

    number: int
    actor: str
    digest: str
    size: int
    submitted_at: float
    archive: str
    status: str = "queued"
    same_as: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None


Grader = Callable[[Submission], Awaitable[tuple[dict[str, Any], dict[str, Any]]]]


class SubmissionQueue:
    """Grade distinct submissions one at a time and report results to every peer.

    The latest submission is the team's final answer. A resubmitted snapshot with
    an identical content digest reuses the earlier grade instead of re-measuring.
    """

    def __init__(
        self, max_pending: int, clock: Callable[[], float] = time.time
    ) -> None:
        """Start an empty ledger with a bound on queued and running gradings."""
        self.max_pending = max_pending
        self.clock = clock
        self.items: list[Submission] = []
        self.announced: dict[str, set[int]] = {}
        self.changed = asyncio.Condition()

    def pending(self) -> int:
        """Count submissions that still wait for, or are in, grading."""
        return sum(item.status in {"queued", "grading"} for item in self.items)

    def origin(self, item: Submission) -> Submission:
        """Follow a duplicate to the submission whose grade it shares."""
        while item.same_as is not None:
            item = self.items[item.same_as - 1]
        return item

    async def submit(
        self, actor: str, digest: str, size: int, archive: str
    ) -> Submission:
        """Record a snapshot, deduplicating identical content, or raise QueueFull."""
        original = next(
            (
                item
                for item in self.items
                if item.digest == digest
                and item.same_as is None
                and item.status != "error"
            ),
            None,
        )
        if original is None and self.pending() >= self.max_pending:
            raise QueueFull(
                f"{self.pending()} submissions are already waiting for grading"
            )
        item = Submission(
            number=len(self.items) + 1,
            actor=actor,
            digest=digest,
            size=size,
            submitted_at=self.clock(),
            archive=archive,
            status="duplicate" if original else "queued",
            same_as=original.number if original else None,
        )
        self.items.append(item)
        async with self.changed:
            self.changed.notify_all()
        return item

    def latest(self) -> Submission | None:
        """Return the team's final answer, which is its most recent submission."""
        return self.items[-1] if self.items else None

    async def work(self, grade: Grader) -> None:
        """Grade queued submissions in order until cancelled, keeping failures as results."""
        while True:
            async with self.changed:
                await self.changed.wait_for(
                    lambda: any(i.status == "queued" for i in self.items)
                )
                item = next(i for i in self.items if i.status == "queued")
                item.status, item.started_at = "grading", self.clock()
            try:
                item.result, item.detail = await grade(item)
                item.status = "graded"
            except asyncio.CancelledError:
                item.status = "interrupted"
                raise
            except Exception as error:
                item.status = "error"
                item.result = {"outcome": "grading_error"}
                item.detail = {
                    "error_type": type(error).__name__,
                    "error": str(error)[:2000],
                }
            finally:
                item.finished_at = self.clock()
                async with self.changed:
                    self.changed.notify_all()

    async def drain(self) -> None:
        """Wait until every queued submission has a result."""
        async with self.changed:
            await self.changed.wait_for(lambda: self.pending() == 0)

    def view(self, actor: str, item: Submission) -> dict[str, Any]:
        """Describe one submission to a peer without trusted grading detail."""
        source = self.origin(item)
        return {
            "number": item.number,
            "by": "you" if item.actor == actor else "a teammate",
            "status": source.status if item.same_as else item.status,
            "same_content_as": item.same_as,
            "result": source.result,
        }

    def unseen(self, actor: str) -> list[dict[str, Any]]:
        """Return results the peer has not yet been told about, and mark them told."""
        told = self.announced.setdefault(actor, set())
        fresh = [
            item
            for item in self.items
            if item.number not in told
            and self.origin(item).status in {"graded", "error"}
        ]
        told.update(item.number for item in fresh)
        return [self.view(actor, item) for item in fresh]

    def summary(self, released: float) -> dict[str, Any]:
        """Report submission timing and in-run grades relative to team release."""
        graded = [i for i in self.items if self.origin(i).status == "graded"]
        valid = [
            i for i in graded if (self.origin(i).result or {}).get("outcome") == "valid"
        ]
        best = max(
            valid,
            key=lambda i: (self.origin(i).result or {}).get("speedup", 0.0),
            default=None,
        )
        latest = self.latest()
        return {
            "count": len(self.items),
            "distinct": sum(i.same_as is None for i in self.items),
            "graded": len(graded),
            "valid": len(valid),
            "errors": sum(i.status == "error" for i in self.items),
            "first_submission_seconds": self.items[0].submitted_at - released
            if self.items
            else None,
            "first_valid_seconds": valid[0].submitted_at - released if valid else None,
            "best_in_run_speedup": (self.origin(best).result or {}).get("speedup")
            if best
            else None,
            "best_submission": best.number if best else None,
            "final_submission": latest.number if latest else None,
            "final_in_run_result": self.origin(latest).result if latest else None,
            "grading_seconds": describe(
                [
                    i.finished_at - i.started_at
                    for i in self.items
                    if i.started_at is not None and i.finished_at is not None
                ]
            ),
            "items": [
                {k: v for k, v in asdict(i).items() if k != "archive"}
                for i in self.items
            ],
        }
