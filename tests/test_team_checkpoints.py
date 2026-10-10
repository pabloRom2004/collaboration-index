"""Exercise harmless peer barrier ordering and failure wakeups for the prototype."""

import asyncio
import time

import pytest

from collaboration_index.checkpoints import TeamBarrier


@pytest.mark.asyncio
async def test_save_waits_for_all_peers_and_commits_once():
    """Keep an early peer blocked until every live peer reaches the same boundary."""
    saves = []

    async def save():
        """Record a completed checkpoint without participant content."""
        saves.append(time.monotonic())

    barrier = TeamBarrier({"one", "two"}, 1, save)
    barrier.last -= 2
    first = asyncio.create_task(barrier.boundary("one"))
    await asyncio.sleep(0)
    assert not first.done() and saves == []
    await barrier.boundary("two")
    await asyncio.wait_for(first, 1)
    assert len(saves) == 1 and barrier.generation == 1


@pytest.mark.asyncio
async def test_finished_peer_does_not_deadlock_waiter():
    """Let the remaining peer save once another peer exhausts its native cap."""
    saves = []

    async def save():
        """Record the checkpoint's finished-peer transition."""
        saves.append(True)

    barrier = TeamBarrier({"one", "two"}, 1, save)
    barrier.last -= 2
    first = asyncio.create_task(barrier.boundary("one"))
    await asyncio.sleep(0)
    await barrier.finished("two")
    await asyncio.wait_for(first, 1)
    assert saves == [True]


@pytest.mark.asyncio
async def test_checkpoint_failure_wakes_every_peer():
    """Propagate checkpoint failure to all waiting peers instead of deadlocking."""

    async def save():
        """Inject an ordinary storage failure at a safe boundary."""
        raise OSError("fixture disk failure")

    barrier = TeamBarrier({"one", "two"}, 1, save)
    barrier.last -= 2
    first = asyncio.create_task(barrier.boundary("one"))
    await asyncio.sleep(0)
    with pytest.raises(OSError):
        await barrier.boundary("two")
    with pytest.raises(RuntimeError, match="aborted"):
        await asyncio.wait_for(first, 1)


@pytest.mark.asyncio
async def test_peer_error_aborts_pending_checkpoint():
    """Cancel a pending barrier without committing a partial team state."""
    saves = []

    async def save():
        """Detect an incorrectly committed partial snapshot."""
        saves.append(True)

    barrier = TeamBarrier({"one", "two"}, 1, save)
    barrier.last -= 2
    first = asyncio.create_task(barrier.boundary("one"))
    await asyncio.sleep(0)
    await barrier.abort(RuntimeError("fixture model failure"))
    with pytest.raises(RuntimeError, match="aborted"):
        await asyncio.wait_for(first, 1)
    assert saves == []


@pytest.mark.asyncio
async def test_solo_and_all_finished_boundaries_do_not_wait():
    """Commit a solo boundary and allow its final transition without a second peer."""
    saves = []

    async def save():
        """Record the solo checkpoint commit."""
        saves.append(True)

    barrier = TeamBarrier({"solo"}, 1, save)
    barrier.last -= 2
    await asyncio.wait_for(barrier.boundary("solo"), 1)
    await asyncio.wait_for(barrier.finished("solo"), 1)
    assert saves == [True] and barrier.live == set()


@pytest.mark.asyncio
async def test_secondary_peer_abort_preserves_first_failure():
    """Keep the original peer exception when released siblings also abort the barrier."""

    async def save():
        """Reject an incorrectly committed partial team state."""
        pytest.fail("An aborted team must not checkpoint")

    barrier = TeamBarrier({"one", "two"}, 1, save)
    barrier.last -= 2
    first = asyncio.create_task(barrier.boundary("one"))
    await asyncio.sleep(0)
    original = OSError("authored original provider failure")
    await barrier.abort(original)
    await barrier.abort(RuntimeError("secondary checkpoint abort"))
    with pytest.raises(RuntimeError, match="aborted") as caught:
        await asyncio.wait_for(first, 1)
    assert caught.value.__cause__ is original
    assert barrier.failure is original


@pytest.mark.asyncio
async def test_peer_abort_preserves_checkpoint_storage_failure():
    """Retain a failed snapshot's cause when later peer cleanup reports an abort."""
    original = OSError("authored checkpoint storage failure")

    async def save():
        """Raise the authored first failure at the coordinated snapshot."""
        raise original

    barrier = TeamBarrier({"one"}, 1, save)
    barrier.last -= 2
    with pytest.raises(OSError):
        await barrier.boundary("one")
    await barrier.abort(RuntimeError("secondary peer abort"))
    assert barrier.failure is original


def test_resume_refuses_missing_private_peer_state():
    """Reject an incomplete checkpoint instead of restarting a peer with a fresh budget."""
    from types import SimpleNamespace

    from collaboration_index.checkpoints import TeamCheckpoints, TeamSnapshot

    class RestoredOwner:
        """Return the authored incomplete checkpoint without accessing storage."""

        attempt = "resume"

        def track(self, key, callback, initial_value):
            """Expose a malformed saved roster for the integrity guard."""
            return TeamSnapshot(run_id="fixture", board={})

    history = SimpleNamespace(run_id="fixture", peers=[SimpleNamespace(id="one")])
    state = SimpleNamespace(store_as=lambda model: history)
    with pytest.raises(ValueError, match="every private peer"):
        TeamCheckpoints(RestoredOwner(), state, 600, 0.75)
