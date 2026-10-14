"""Check GPU leases, token budgets, submissions and the pod helpers of the leased-GPU mode."""

import asyncio
import io
import json
import os
import random
import signal
import socket
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from collaboration_index.inferencebench import pod
from collaboration_index.inferencebench.orchestration import (
    GpuScheduler,
    GpuUsage,
    QueueFull,
    Submission,
    SubmissionQueue,
    describe,
)
from collaboration_index.inferencebench.prompts import leased_gpu_prompt
from collaboration_index.inferencebench.session import classify_final
from collaboration_index.inferencebench.task import budget_plan


class Clock:
    """Provide a manually advanced clock."""

    def __init__(self) -> None:
        """Start at time zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


@pytest.mark.parametrize("access", ["scheduler", "unmanaged"])
@pytest.mark.parametrize("scoring", ["best_submit", "last_submit"])
def test_leased_gpu_prompt_has_team_wording_only_for_teams(
    access: str, scoring: str
) -> None:
    """Fill every placeholder, describe the access mode and scoring rule, and keep team sentences out of the solo prompt."""
    options = {
        "workspace_gpus": 2,
        "gpu_access": access,
        "scoring": scoring,
        "lease_seconds": 1800,
        "max_pending_submissions": 3,
        "dev_eval": {
            "requests": 5,
            "quick_requests": 2,
            "quality_samples": 100,
            "quick_quality_samples": 16,
        },
    }
    solo: Any = SimpleNamespace(options=options, budget=budget_plan("shared", 1000, 1))
    team: Any = SimpleNamespace(options=options, budget=budget_plan("shared", 1000, 4))
    solo_text, team_text = leased_gpu_prompt(1, solo), leased_gpu_prompt(4, team)
    for word in ["teammate", "another agent", "All agents", "Any agent", "pool"]:
        assert word not in solo_text, word
    assert "pool of 1,000 tokens" in team_text
    assert "{" not in solo_text + team_text
    leases = "the gpu tool gives you exclusive GPUs"
    assert (leases in team_text) == (access == "scheduler")
    assert ("nothing reserves a GPU" in team_text) == (access == "unmanaged")
    best = "highest speedup sets the score"
    assert (best in team_text) == (scoring == "best_submit")
    assert ("most recent valid submission" in team_text) == (scoring == "last_submit")


def test_budget_plan_modes_and_validation() -> None:
    """Split the team total into equal caps or keep one pool, and reject impossible budgets."""
    split = budget_plan("split", 1000, 3)
    assert split["per_agent_cap"] == 333 and split["planned_team_total"] == 999
    assert split["unallocated_tokens"] == 1
    assert budget_plan("split", 4_000_000, 64)["unallocated_tokens"] == 0
    shared = budget_plan("shared", 1000, 3)
    assert shared["per_agent_cap"] is None and shared["team_pool"] == 1000
    assert shared["planned_team_total"] == 1000
    for args in [("per_agent", 10, 2), ("shared", 0, 2), ("split", 3, 4)]:
        with pytest.raises(ValueError):
            budget_plan(*args)


async def test_waiters_are_served_in_order_and_withdrawn_on_timeout() -> None:
    """Serve waiters for one GPU in arrival order and withdraw a request that times out."""
    scheduler = GpuScheduler(slots=1, lease_seconds=60)
    first = await scheduler.acquire("a", 0)
    assert first is not None and first.slots == [0]
    assert await scheduler.acquire("c", 0) is None
    later = asyncio.ensure_future(scheduler.acquire("b", 5))
    await asyncio.sleep(0)
    third = asyncio.ensure_future(scheduler.acquire("c", 5))
    await asyncio.sleep(0)
    assert scheduler.status("c", {})["your_queue_position"] == 2
    assert scheduler.release("a") == [0]
    granted = await later
    assert granted is not None and granted.actor == "b"
    assert not third.done()
    scheduler.release("b")
    assert (await third).actor == "c"
    assert len(scheduler.timeouts) == 1
    assert scheduler.take_cleanup(0) == ({first.token, granted.token}, set())


async def test_leases_grow_while_nobody_waits() -> None:
    """Lend free GPUs to existing leases, never beyond the pool, and keep a returned GPU's processes."""
    scheduler = GpuScheduler(slots=4, lease_seconds=60)
    a = await scheduler.acquire("a", 0, count=3, note="tensor parallel test")
    assert a is not None and a.slots == [0, 1, 2]
    b = await scheduler.acquire("b", 0, count=2)
    assert b is not None and b.slots == [3]
    assert scheduler.release("a", slot=1) == [1]
    assert scheduler.take_cleanup(1) == (set(), {a.token})
    assert (await scheduler.acquire("b", 0, count=2)).slots == [3, 1]
    assert (await scheduler.acquire("a", 0, count=4)).slots == [0, 2]
    assert scheduler.release("a", slot=3) == []
    assert scheduler.status("b", {})["free_gpus"] == 0
    # a GPU that returns to the same lease keeps that lease's processes
    assert scheduler.release("a", slot=2) == [2]
    assert (await scheduler.acquire("a", 0, count=2)).slots == [0, 2]
    assert scheduler.take_cleanup(2) == (set(), set())


async def test_extra_gpus_go_back_first_and_never_from_a_protected_lease() -> None:
    """Take back every extra GPU waiters need in one step once the holder is unprotected, then expire."""
    clock = Clock()
    scheduler = GpuScheduler(slots=3, lease_seconds=60, clock=clock)
    a = await scheduler.acquire("a", 0, count=3)
    assert a is not None and a.slots == [0, 1, 2] and scheduler.protect(a)
    waits = [asyncio.ensure_future(scheduler.acquire(actor, 600)) for actor in "bcd"]
    await asyncio.sleep(0)
    assert len(scheduler.waiting) == 3 and a.slots == [0, 1, 2]
    clock.now = 5
    scheduler.unprotect(a)
    assert [w.actor for w in scheduler.waiting] == ["d"]
    assert a.slots == [0] and a.reclaimed == 2 and a.ended_at is None
    assert [(await w).slots for w in waits[:2]] == [[1], [2]]
    assert [scheduler.take_cleanup(s) for s in (1, 2)] == [(set(), {a.token})] * 2
    clock.now = 61
    scheduler.tick()
    d = await waits[2]
    assert d.slots == [0] and a.end_reason == "expired"
    summary = scheduler.summary()
    assert summary["extra_gpus_reclaimed"] == 2
    assert summary["max_gpus_per_lease"] == 3


async def test_every_waiter_gets_one_gpu_before_anyone_gets_extras() -> None:
    """Serve each waiting agent one GPU before a waiter that asked for several gets more."""
    scheduler = GpuScheduler(slots=2, lease_seconds=60)
    holder = await scheduler.acquire("holder", 0, count=2)
    assert holder is not None and scheduler.protect(holder)
    first = asyncio.ensure_future(scheduler.acquire("a", 30, count=2))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(scheduler.acquire("b", 30))
    await asyncio.sleep(0)
    assert holder.slots == [0, 1]
    scheduler.release("holder")
    scheduler.unprotect(holder)
    a, b = await first, await second
    assert a.slots == [0] and b.slots == [1]


async def test_lease_expiry_needs_contention_and_waits_for_protection() -> None:
    """Keep an old lease without waiters and reassign it only after its protection ends."""
    clock = Clock()
    scheduler = GpuScheduler(slots=1, lease_seconds=10, clock=clock)
    lease = await scheduler.acquire("a", 0)
    assert lease is not None
    clock.now = 100
    scheduler.tick()
    assert scheduler.holders[0] is lease
    assert scheduler.protect(lease)
    waiting = asyncio.ensure_future(scheduler.acquire("b", 30))
    await asyncio.sleep(0)
    assert scheduler.holders[0] is lease and not waiting.done()
    scheduler.unprotect(lease)
    granted = await waiting
    assert granted is not None and granted.actor == "b"
    assert lease.end_reason == "expired" and lease.ended_at == 100
    assert not scheduler.protect(lease)


async def test_cancelled_and_finished_requests_free_their_gpus() -> None:
    """Withdraw cancelled and finished requests, and release a grant that raced a cancellation."""
    scheduler = GpuScheduler(slots=1, lease_seconds=60)
    first = await scheduler.acquire("a", 0)
    assert first is not None
    waiting = asyncio.ensure_future(scheduler.acquire("b", 30))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not scheduler.waiting
    # the grant completes, but its only caller is cancelled before it resumes
    racing = asyncio.ensure_future(scheduler.acquire("b", 30))
    await asyncio.sleep(0)
    scheduler.release("a")
    granted = scheduler.leases[-1]
    assert granted.actor == "b" and scheduler.holders[0] is granted
    racing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await racing
    assert granted.end_reason == "cancelled" and not scheduler.holders
    assert scheduler.take_cleanup(0) == ({first.token, granted.token}, set())
    # a request still waiting when its peer finishes returns no lease
    held = await scheduler.acquire("b", 0)
    withdrawn = asyncio.ensure_future(scheduler.acquire("c", 30))
    await asyncio.sleep(0)
    scheduler.finish("c")
    assert await withdrawn is None and not scheduler.waiting
    assert scheduler.finish("b") == [0] and held.end_reason == "finished"


async def test_concurrent_calls_by_one_agent_share_one_place() -> None:
    """Keep one queue entry per agent, so a duplicate call never blocks growth or takes extras."""
    scheduler = GpuScheduler(slots=2, lease_seconds=60)
    a = await scheduler.acquire("a", 0)
    c = await scheduler.acquire("c", 0)
    first = asyncio.ensure_future(scheduler.acquire("b", 30))
    second = asyncio.ensure_future(scheduler.acquire("b", 30, count=2))
    third = asyncio.ensure_future(scheduler.acquire("b", 30))
    await asyncio.sleep(0)
    assert [w.actor for w in scheduler.waiting] == ["b"]
    assert scheduler.status("a", {})["waiting_agents"] == 1
    # a caller that gives up early does not withdraw the shared request
    assert await scheduler.acquire("b", 0) is None
    third.cancel()
    await asyncio.sleep(0)
    assert len(scheduler.waiting) == 1 and not scheduler.timeouts
    scheduler.release("a")
    b = await first
    assert b is not None and await second is b and b.slots == [0]
    assert not scheduler.waiting and scheduler.held("b") is b
    assert a.ended_at is not None and c.ended_at is None
    scheduler.release("c")
    assert (await scheduler.acquire("b", 0, count=2)).slots == [0, 1]


async def test_failed_cleanup_is_restored_except_for_a_returning_lease() -> None:
    """Put failed cleanup work back, but not for the lease that holds the GPU again."""
    scheduler = GpuScheduler(slots=2, lease_seconds=60)
    lease = await scheduler.acquire("a", 0, count=2)
    assert lease is not None and scheduler.release("a", slot=1) == [1]
    stale, shrunk = scheduler.take_cleanup(1)
    assert not scheduler.has_cleanup(1)
    scheduler.restore_cleanup(1, stale, shrunk)
    assert scheduler.has_cleanup(1) and scheduler.shrunk == {1: {lease.token}}
    stale, shrunk = scheduler.take_cleanup(1)
    assert (await scheduler.acquire("a", 0, count=2)).slots == [0, 1]
    scheduler.restore_cleanup(1, stale, shrunk)
    assert not scheduler.has_cleanup(1)


async def test_status_names_and_summary_counts() -> None:
    """Name holders by board name only, show notes, and count occupancy inside the interval."""
    clock = Clock()
    scheduler = GpuScheduler(slots=2, lease_seconds=60, clock=clock)
    await scheduler.acquire("agent_0", 0, note="fp8 sweep")
    status = scheduler.status("agent_1", {"agent_0": "Quanta"})
    assert status["gpus"][0]["holder"] == "Quanta"
    assert status["gpus"][0]["note"] == "fp8 sweep"
    assert status["gpus"][1]["holder"] is None and not status["your_gpus"]
    assert "agent_0" not in json.dumps(scheduler.status("agent_1", {}))
    assert scheduler.status("agent_0", {})["gpus"][0]["holder"] == "you"
    clock.now = 50
    scheduler.release("agent_0")
    summary = scheduler.summary(end=100)
    assert summary["grants"] == 1 and summary["end_reasons"] == {"released": 1}
    assert summary["occupancy"] == pytest.approx(0.25)
    assert summary["gpu_hold_seconds"]["max"] == 50
    # holding time before the interval does not count
    assert scheduler.summary(start=25, end=100)["occupancy"] == pytest.approx(25 / 150)
    late = scheduler.summary(start=60, end=100)
    assert late["occupancy"] == 0 and late["gpu_hold_seconds"]["count"] == 0
    assert describe([])["count"] == 0
    assert describe([3.0, 1.0, 2.0])["p50"] == 2.0


@pytest.mark.parametrize(("slots", "agents"), [(1, 8), (2, 8), (4, 4), (8, 3)])
async def test_random_agents_keep_gpus_exclusive(slots: int, agents: int) -> None:
    """Let random agents acquire, grow, measure, submit, release and abandon GPUs without overlap."""
    rng = random.Random(slots * 10 + agents)
    scheduler = GpuScheduler(slots=slots, lease_seconds=0.004)
    queue = SubmissionQueue(max_pending=3)
    using: dict[int, str] = {}
    most = [0]

    async def grade(item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Take a little time to grade."""
        await asyncio.sleep(0.001)
        return {"outcome": "valid", "speedup": 1.0}, {}

    def check_pool() -> None:
        """Assign each slot to at most one lease and queue each agent at most once."""
        leases = {id(lease): lease for lease in scheduler.holders.values()}.values()
        owned = [s for lease in leases for s in lease.slots]
        assert len(owned) == len(set(owned)) == len(scheduler.holders)
        assert all(scheduler.holders[s].slots.count(s) == 1 for s in owned)
        queued = [w.actor for w in scheduler.waiting]
        assert len(queued) == len(set(queued))
        assert not any(scheduler.held(actor) for actor in queued)
        assert all(
            scheduler.holders[s].token not in scheduler.shrunk.get(s, set())
            for s in owned
        )
        most[0] = max([most[0]] + [len(lease.slots) for lease in leases])

    async def agent(actor: str) -> None:
        """Perform random GPU, measurement and submission steps."""
        try:
            for step in range(25):
                # sometimes the same agent calls acquire twice at once
                wait = rng.choice([0, 0.002, 0.05])
                calls = [
                    scheduler.acquire(actor, wait, count=rng.randint(1, slots))
                    for _ in range(rng.choice([1, 1, 2]))
                ]
                results = await asyncio.gather(*calls)
                # both calls return one lease, unless it expired between them
                active = {id(r): r for r in results if r and r.ended_at is None}
                assert len(active) <= 1
                lease = next(iter(active.values()), None)
                check_pool()
                if lease is not None and scheduler.protect(lease):
                    mine = list(lease.slots)
                    try:
                        for slot in mine:
                            assert using.get(slot) is None, "two agents used one GPU"
                            using[slot] = actor
                        await asyncio.sleep(rng.random() * 0.003)
                        assert lease.slots == mine, "a protected lease lost a GPU"
                    finally:
                        for slot in mine:
                            using.pop(slot, None)
                        scheduler.unprotect(lease)
                if rng.random() < 0.2:
                    try:
                        await queue.submit(actor, f"{actor}-{step % 3}", 1, "x")
                    except QueueFull:
                        pass
                held = scheduler.held(actor)
                if held is not None and rng.random() < 0.5:
                    scheduler.release(actor, rng.choice([None, *held.slots]))
                check_pool()
                await asyncio.sleep(rng.random() * 0.002)
        finally:
            scheduler.finish(actor)

    worker = asyncio.ensure_future(queue.work(grade))
    tasks = [asyncio.ensure_future(agent(f"agent_{i}")) for i in range(agents)]
    await asyncio.sleep(0.05)
    tasks[0].cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(r is None or isinstance(r, asyncio.CancelledError) for r in results), (
        results
    )
    await asyncio.wait_for(queue.drain(), 10)
    worker.cancel()
    assert not scheduler.holders and not scheduler.waiting and not scheduler.busy
    assert all(lease.ended_at for lease in scheduler.leases)
    work = [scheduler.take_cleanup(s) for s in range(slots)]
    everyone = {lease.token for lease in scheduler.leases}
    assert set().union(*[stale for stale, _ in work]) == everyone
    assert set().union(*[shrunk for _, shrunk in work]) <= everyone
    assert "active" not in scheduler.summary()["end_reasons"]
    if agents <= slots:
        assert most[0] > 1, "no agent ever held several GPUs"
    assert all(item.status in {"graded", "duplicate"} for item in queue.items)


def test_gpu_usage_weights_probes_and_reports_pressure_once() -> None:
    """Count busy and leased time, report one pressure event per crossing and sum energy."""
    usage = GpuUsage(interval=3600)

    def sample(util: float | None, used: float, power: float | None) -> dict[str, Any]:
        """Build one probe of a single 100 MiB GPU."""
        return {
            "gpus": [
                {
                    "index": 0,
                    "utilization_percent": util,
                    "memory_used_mib": used,
                    "memory_total_mib": 100.0,
                    "power_watts": power,
                }
            ]
        }

    assert usage.add(sample(50, 98, 300), {0: "t"}, 0, None) == [
        {"gpu": 0, "memory_used_mib": 98, "memory_total_mib": 100.0}
    ]
    assert usage.add(sample(0, 99, 100), {}, 2, None) == []
    usage.add(sample(None, 10, None), {}, 0, None)
    assert len(usage.add(sample(80, 97, 200), {0: "t"}, 0, None)) == 1
    summary = usage.summary()
    gpu = summary["gpus"][0]
    assert gpu["busy_fraction"] == 0.5 and gpu["leased_fraction"] == 0.5
    assert gpu["busy_while_leased_fraction"] == 1.0
    assert gpu["mean_utilization_percent"] == pytest.approx(130 / 3)
    assert gpu["peak_memory_mib"] == 99
    assert summary["busy_gpu_hours"] == 2 and summary["observed_gpu_hours"] == 4
    assert summary["energy_watt_hours"] == 600
    assert summary["unleased_gpu_process_observations"] == 2
    weighted = GpuUsage(interval=10)
    weighted.add(sample(100, 10, 400), {}, 0, 30)
    weighted.add(sample(0, 10, 100), {}, 0, 10)
    result = weighted.summary()
    assert result["gpus"][0]["busy_fraction"] == 0.75
    assert result["observed_gpu_hours"] == pytest.approx(40 / 3600)
    assert result["energy_watt_hours"] == pytest.approx(13000 / 3600)


async def test_submissions_are_graded_in_order_and_duplicates_reuse_grades() -> None:
    """Grade distinct snapshots in order, share a duplicate's grade and announce once per peer."""
    clock = Clock()
    queue = SubmissionQueue(max_pending=2, clock=clock)
    graded: list[int] = []

    async def grade(item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a fixed result in grading order."""
        graded.append(item.number)
        clock.now += 10
        return {"outcome": "valid", "speedup": float(item.number)}, {"n": item.number}

    first = await queue.submit("a", "d1", 10, "one.tar.gz")
    await queue.submit("b", "d2", 10, "two.tar.gz")
    with pytest.raises(QueueFull):
        await queue.submit("a", "d3", 10, "three.tar.gz")
    again = await queue.submit("b", "d1", 10, "again.tar.gz")
    assert again.status == "duplicate" and again.same_as == first.number
    worker = asyncio.ensure_future(queue.work(grade))
    await queue.drain()
    worker.cancel()
    assert graded == [1, 2]
    assert queue.latest() is again
    assert queue.view("a", again)["result"] == {"outcome": "valid", "speedup": 1.0}
    notices = queue.unseen("a")
    assert [n["number"] for n in notices] == [1, 2, 3]
    assert notices[0]["by"] == "you" and notices[1]["by"] == "a teammate"
    assert queue.unseen("a") == []
    summary = queue.summary(released=0)
    assert summary["count"] == 3 and summary["distinct"] == 2
    assert summary["best_in_run_speedup"] == 2.0 and summary["latest_submission"] == 3
    # best_submit: the duplicate #3 shares #1's 1.0, so #2 sets the score
    assert summary["scoring"] == "best_submit" and summary["final_submission"] == 2
    assert summary["first_valid_seconds"] == 0
    assert all("archive" not in item for item in summary["items"])


@pytest.mark.parametrize(
    ("scoring", "expected"), [("best_submit", 2), ("last_submit", 4)]
)
async def test_scoring_rules_select_only_valid_submissions(
    scoring: str, expected: int
) -> None:
    """Select the earliest best or the last valid submission and never an invalid or failed one."""
    queue = SubmissionQueue(max_pending=10, scoring=scoring)
    results: dict[int, dict[str, Any] | Exception] = {
        1: {"outcome": "valid", "speedup": 1.5},
        2: {"outcome": "valid", "speedup": 3.0},
        3: {"outcome": "quality_failed", "speedup": 1.0},
        4: {"outcome": "valid", "speedup": 3.0},
        5: {"outcome": "invalid_submission", "speedup": 1.0},
        6: RuntimeError("authored grading failure"),
    }

    async def grade(item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the authored result for each submission number."""
        result = results[item.number]
        if isinstance(result, Exception):
            raise result
        return result, {}

    assert queue.select() is None
    for number in results:
        await queue.submit("a", f"d{number}", 1, f"{number}.tar.gz", {"n": number})
    worker = asyncio.ensure_future(queue.work(grade))
    await queue.drain()
    worker.cancel()
    selected = queue.select()
    assert selected is not None and selected.number == expected
    assert selected.measures == {"n": expected}
    summary = queue.summary(released=0)
    assert summary["final_submission"] == expected
    assert summary["final_in_run_result"]["speedup"] == 3.0
    assert summary["latest_submission"] == 6 and summary["valid"] == 3
    with pytest.raises(ValueError, match="scoring"):
        SubmissionQueue(max_pending=1, scoring="first_submit")


async def test_no_valid_submission_selects_nothing() -> None:
    """Leave the score at the fallback when every submission is invalid."""
    queue = SubmissionQueue(max_pending=2, scoring="last_submit")

    async def grade(item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fail the quality gate for every submission."""
        return {"outcome": "quality_failed", "speedup": 1.0}, {}

    await queue.submit("a", "d1", 1, "1.tar.gz")
    worker = asyncio.ensure_future(queue.work(grade))
    await queue.drain()
    worker.cancel()
    assert queue.select() is None
    assert queue.summary(released=0)["final_submission"] is None


async def test_failed_grading_is_kept_and_the_same_content_is_regraded() -> None:
    """Keep a failed grading as an error result and grade the same content again later."""
    queue = SubmissionQueue(max_pending=4)
    calls = []

    async def grade(item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Raise on the first call and succeed afterwards."""
        calls.append(item.number)
        if len(calls) == 1:
            raise RuntimeError("authored pod failure")
        return {"outcome": "valid", "speedup": 1.5}, {}

    worker = asyncio.ensure_future(queue.work(grade))
    await queue.submit("a", "same", 1, "x")
    await queue.drain()
    retry = await queue.submit("a", "same", 1, "y")
    assert retry.same_as is None
    await queue.drain()
    worker.cancel()
    assert queue.items[0].status == "error"
    assert queue.items[0].result == {"outcome": "grading_error"}
    assert queue.items[0].detail["error_type"] == "RuntimeError"
    assert queue.items[1].status == "graded"
    assert queue.summary(released=0)["errors"] == 1


def test_classify_final_follows_the_upstream_score_rules() -> None:
    """Map invalid, quality-failed, empty, undefined and valid measurements to peer-visible scores."""

    def measured(throughput: float, successes: int = 3) -> dict[str, Any]:
        """Build scenario C metrics with one request profile."""
        return {
            "profiles": {
                "p": {
                    "success_count": successes,
                    "request_throughput_req_per_s": throughput,
                }
            },
            "quality_check": {"pass": True},
        }

    base = measured(2.0)
    assert classify_final({"invalid_submission": "x"}, base, "C") == {
        "outcome": "invalid_submission",
        "speedup": 1.0,
    }
    failed = {**measured(5.0), "quality_check": {"pass": False}}
    assert classify_final(failed, base, "C")["outcome"] == "quality_failed"
    empty = measured(5.0, successes=0)
    assert classify_final(empty, base, "C")["outcome"] == "no_successful_requests"
    assert classify_final(measured(5.0), base, "C") == {
        "outcome": "valid",
        "speedup": 2.5,
        "quality_pass": True,
    }
    undefined = classify_final(measured(0.0), base, "C")
    assert undefined["outcome"] == "undefined_performance"
    with pytest.raises(RuntimeError):
        classify_final(
            {**measured(5.0), "quality_check": {"pass": False, "error": "registry"}},
            base,
            "C",
        )
    with pytest.raises(RuntimeError, match="no quality result"):
        classify_final({"profiles": base["profiles"]}, base, "C")


SMI = (
    "0, 00000000:18:00.0, 1024, 81559, 3, 71.5\n"
    "1, 00000000:2A:00.0, [N/A], 81559, 97, [N/A]\n"
    "garbage line\n"
)
QUERY = (
    "GPU 00000000:18:00.0\n    Minor Number                          : 4\n"
    "GPU 00000000:2A:00.0\n    Minor Number                          : 7\n"
)


def fake_query(command: list[str]) -> str:
    """Return authored nvidia-smi output for the two supported queries."""
    return QUERY if command[-1] == "-q" else SMI


def add_process(
    proc: Path,
    pid: int,
    devices: list[str],
    token: str | None,
    visible: bytes | None = None,
) -> None:
    """Write one fake /proc entry with device handles, a lease token and CUDA_VISIBLE_DEVICES."""
    folder = proc / str(pid)
    (folder / "fd").mkdir(parents=True)
    for number, device in enumerate(devices):
        os.symlink(device, folder / "fd" / str(number))
    environ = [b"PATH=/bin"]
    environ += [f"{pod.LEASE_VAR}={token}".encode()] if token else []
    environ += [b"CUDA_VISIBLE_DEVICES=" + visible] if visible is not None else []
    (folder / "environ").write_bytes(b"\0".join(environ) + b"\0")
    (folder / "cmdline").write_bytes(f"python\0serve-{pid}\0".encode())
    (folder / "status").write_text("Name: fixture\nPPid:\t1\n")


def fake_proc(root: Path) -> Path:
    """Create a /proc tree with GPU handles, lease tokens and an unrelated process."""
    proc = root / "proc"
    add_process(proc, 11, ["/dev/nvidia7", "/dev/nvidiactl"], "tok-b", b"1")
    add_process(proc, 12, ["/dev/nvidia4"], None)
    add_process(proc, 13, [], "tok-a")
    add_process(proc, 14, ["/dev/null"], None)
    (proc / "self").mkdir()
    return proc


def test_oom_signatures() -> None:
    """Recognize common CUDA, PyTorch and vLLM allocation failures only."""
    assert pod.detect_oom("torch.OutOfMemoryError: CUDA out of memory. Tried")
    assert pod.detect_oom("ValueError: No available memory for the cache blocks")
    assert pod.detect_oom("RuntimeError: CUBLAS_STATUS_ALLOC_FAILED")
    assert pod.detect_oom("server ready on port 8000") is None


def test_probe_attributes_processes_to_indices(tmp_path: Path) -> None:
    """Map device minors to nvidia-smi indices and find GPU use outside a lease."""
    sample = pod.probe(str(fake_proc(tmp_path)), fake_query)
    assert [g["memory_used_mib"] for g in sample["gpus"]] == [1024.0, None]
    assert [g["power_watts"] for g in sample["gpus"]] == [71.5, None]
    by_pid = {p["pid"]: p for p in sample["processes"]}
    assert set(by_pid) == {11, 12, 13}
    assert by_pid[11]["gpus"] == [1] and by_pid[12]["gpus"] == [0]
    assert by_pid[13]["gpus"] == [] and by_pid[13]["token"] == "tok-a"
    assert by_pid[11]["visible"] == [1] and by_pid[12]["visible"] is None
    violations = pod.audit(sample, {1: "tok-b", 0: "tok-a"})
    assert [(v["gpu"], v["pid"]) for v in violations] == [(0, 12)]
    assert len(pod.audit(sample, {})) == 2


def test_audit_ignores_enumeration_and_flags_misuse() -> None:
    """Flag tokenless, stale, foreign-only and shrink-leaked GPU use, and nothing else."""

    def process(
        pid: int,
        gpus: list[int],
        token: str | None,
        command: str,
        visible: list[int] | None = None,
    ) -> dict[str, Any]:
        """Build one attributed process."""
        return {
            "pid": pid,
            "gpus": gpus,
            "token": token,
            "command": command,
            "visible": visible,
        }

    sample = {
        "processes": [
            process(1, [0, 1], None, "/usr/bin/nvidia-smi -q"),
            process(2, [0, 1], "tok-a", "python serve", [0]),
            process(3, [1], "tok-a", "python sneak", [0]),
            process(4, [1], "tok-old", "python stale"),
            process(5, [0], None, "python unleased"),
            # started while the lease also held GPU 1, which it later gave up
            process(6, [0], "tok-a", "python leaked", [0, 1]),
            # without CUDA_VISIBLE_DEVICES the opened GPUs decide
            process(7, [0, 1], "tok-a", "python unrestricted"),
            # a CPU-only job uses no GPU, whatever it could see
            process(8, [], "tok-a", "pip download", [0, 1]),
        ]
    }
    flagged = sorted((v["pid"], v["gpu"]) for v in pod.audit(sample, {0: "tok-a"}))
    assert flagged == [(3, 1), (4, 1), (5, 0), (6, 1), (7, 1)]
    both = pod.audit(sample, {0: "tok-a", 1: "tok-a"})
    assert sorted((v["pid"], v["gpu"]) for v in both) == [(4, 1), (5, 0)]
    assert pod.parse_visible(b"") == [] and pod.parse_visible(b"0,,1") == [0, 1]
    for value in [b"GPU-1a2b", b"MIG-3c4d", b"-1", b"0,x"]:
        assert pod.parse_visible(value) is None, value


def test_kill_by_token_gpu_and_pid(tmp_path: Path) -> None:
    """Signal token and GPU holders and escalate only for survivors."""
    proc = str(fake_proc(tmp_path))
    sent: list[tuple[int, int]] = []
    killed = pod.kill(
        ["tok-a"],
        [0],
        proc=proc,
        query=fake_query,
        send=lambda pid, sig: sent.append((pid, sig)),
        exists=lambda pid: pid == 12,
        grace=0.0,
    )
    assert sorted(killed) == [12, 13]
    assert (12, signal.SIGKILL) in sent and (13, signal.SIGKILL) not in sent
    assert pod.kill(["absent"], [], proc=proc, send=lambda *a: None) == []
    assert pod.kill(
        [], [], pids=[11], proc=proc, send=lambda *a: None, exists=lambda pid: False
    ) == [11]


def test_scoped_kill_spares_other_gpus_and_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stop only a shrunk lease's processes started for the taken GPU, also through the CLI."""
    proc = fake_proc(tmp_path)
    # GPU 0 is /dev/nvidia4 and GPU 1 is /dev/nvidia7 in fake_query
    both = ["/dev/nvidia4", "/dev/nvidia7"]
    add_process(proc, 15, both, "tok-a", b"0,1")  # spans both GPUs
    add_process(proc, 16, both, "tok-a", b"0")  # GPU 0 server that enumerated GPU 1
    add_process(proc, 17, ["/dev/nvidia7"], "tok-b", b"1")  # the GPU's new holder
    add_process(proc, 18, ["/dev/nvidia7"], "tok-a")  # no CUDA_VISIBLE_DEVICES
    add_process(proc, 19, [], "tok-a", b"0,1")  # CPU-only download
    killed = pod.kill(
        [],
        [],
        scoped=[("tok-a", 1)],
        proc=str(proc),
        query=fake_query,
        send=lambda *a: None,
        exists=lambda pid: False,
    )
    assert killed == [15, 18]
    calls: list[tuple[Any, ...]] = []

    def record(*args: Any) -> list[int]:
        """Capture the parsed kill arguments."""
        calls.append(args)
        return []

    monkeypatch.setattr(pod, "kill", record)
    pod.main(["kill", "--token=t", "--scoped=tok-a:1", "--scoped=tok-c:3"])
    assert calls == [(["t"], [], [], [], [("tok-a", 1), ("tok-c", 3)])]
    assert json.loads(capsys.readouterr().out) == {"killed": []}


def test_kill_port_listeners(tmp_path: Path) -> None:
    """Find and stop a process listening on the server port, ignoring other sockets."""
    proc = fake_proc(tmp_path)
    (proc / "net").mkdir()
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (proc / "net" / "tcp").write_text(
        header
        + "   0: 0100007F:1F40 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4242\n"
        + "   1: 0100007F:1F41 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4343\n"
        + "   2: 0100007F:1F40 0100007F:9C40 01 00000000:00000000 00:00000000 00000000     0        0 4444\n"
    )
    for pid, inode in [(21, 4242), (22, 4343), (23, 4444)]:
        (proc / str(pid) / "fd").mkdir(parents=True)
        os.symlink(f"socket:[{inode}]", proc / str(pid) / "fd" / "5")
        (proc / str(pid) / "environ").write_bytes(b"")
        (proc / str(pid) / "cmdline").write_bytes(b"daemon")
    assert pod.listeners({8000}, str(proc)) == {21}
    killed = pod.kill(
        [],
        [],
        ports=[8000],
        proc=str(proc),
        send=lambda *a: None,
        exists=lambda pid: False,
    )
    assert killed == [21]


def test_pack_digest_ignores_mtime_and_enforces_the_size_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Give equal content one digest, change it with mode or bytes, and exit TOO_LARGE above the limit."""
    source = tmp_path / "task"
    (source / "sub").mkdir(parents=True)
    (source / "start_server.sh").write_text("#!/bin/sh\nexec serve\n")
    (source / "sub" / "weights.bin").write_bytes(b"\x00" * 64)
    os.symlink("/opt/hf_cache", source / "cache")
    first = pod.pack(str(source), str(tmp_path / "out" / "a.tar.gz"), 10_000)
    os.utime(source / "start_server.sh", (1, 1))
    again = pod.pack(str(source), str(tmp_path / "b.tar.gz"), 10_000)
    assert again["digest"] == first["digest"]
    (source / "start_server.sh").chmod(0o755)
    moded = pod.pack(str(source), str(tmp_path / "c.tar.gz"), 10_000)
    assert moded["digest"] != first["digest"] and moded["files"] == 4
    with pytest.raises(SystemExit) as exit_info:
        pod.main(["pack", str(source), str(tmp_path / "d.tar.gz"), "--max-bytes", "10"])
    assert exit_info.value.code == pod.TOO_LARGE
    assert "holds more than 10 bytes" in capsys.readouterr().err


def test_pack_skips_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive a task directory that contains a UNIX socket without failing."""
    source = tmp_path / "task"
    source.mkdir()
    (source / "start_server.sh").write_text("serve")
    monkeypatch.chdir(source)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind("engine.sock")
    try:
        result = pod.pack(str(source), str(tmp_path / "s.tar.gz"), 10_000)
    finally:
        listener.close()
    assert result["files"] == 1


def test_unpack_replaces_and_refuses_escape(tmp_path: Path) -> None:
    """Restore an archive over stale files and reject members outside the destination."""
    source = tmp_path / "task"
    source.mkdir()
    (source / "start_server.sh").write_text("serve")
    os.symlink("/opt/hf_cache", source / "cache")
    pod.pack(str(source), str(tmp_path / "s.tar.gz"), 10_000)
    destination = tmp_path / "restore"
    (destination / "stale").mkdir(parents=True)
    pod.unpack(str(tmp_path / "s.tar.gz"), str(destination))
    assert sorted(p.name for p in destination.iterdir()) == ["cache", "start_server.sh"]
    assert os.readlink(destination / "cache") == "/opt/hf_cache"
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as archive:
        info = tarfile.TarInfo("../escaped")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(tarfile.FilterError):
        pod.unpack(str(evil), str(destination))
    assert not (tmp_path / "escaped").exists()


def test_evaluator_wrapper_compiles_with_quick_override_first() -> None:
    """Override the quality count for quick runs before environment defaults apply."""
    text = pod.evaluator_wrapper({"INFERENCE_BENCH_QUALITY_MMLUPRO_N": "100"}, 16)
    compile(text, "evaluate.py", "exec")
    assert text.index("--quick") < text.index("setdefault")
    assert "'--server-url'" in text and "PORT" in text
