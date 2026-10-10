"""Verify harmless SQLite checkpoint identity, delivery and committed-write deduplication."""

import secrets

import pytest

from collaboration_index.board.checkpoint import restore_board, snapshot_board
from collaboration_index.board.client import LIMITS
from collaboration_index.board.database import BoardDatabase, BoardError, token_hash


def populated_board(tmp_path):
    """Create two harmless authenticated peers with one delivered message."""
    path = tmp_path / "original.sqlite"
    database = BoardDatabase(f"sqlite:///{path}", LIMITS)
    tokens = {peer: secrets.token_urlsafe(32) for peer in ("one", "two")}
    database.provision(
        {"fixture": {peer: token_hash(token) for peer, token in tokens.items()}}
    )
    for peer in tokens:
        database.execute(
            "fixture",
            peer,
            {"request_id": f"register-{peer}", "action": "register", "name": peer},
        )
    request = {
        "request_id": "stable-send",
        "action": "send",
        "message": "harmless checkpoint fixture",
    }
    result = database.execute("fixture", "one", request)
    assert database.unread("fixture", "two")["global"] == 1
    database.execute("fixture", "two", {"request_id": "read", "action": "read"})
    return path, database, tokens, request, result


def test_restore_preserves_identity_unread_and_write_deduplication(tmp_path):
    """Restore committed board state with rotated auth and exactly-once send replay."""
    path, original, old, request, result = populated_board(tmp_path)
    bundle = snapshot_board(path, 1_000_000)
    new = {peer: secrets.token_urlsafe(32) for peer in old}
    target = tmp_path / "new.sqlite"
    restore_board(bundle, target, "fixture", new, 1_000_000)
    restored = BoardDatabase(f"sqlite:///{target}", LIMITS)
    for peer in new:
        assert restored.authenticate(new[peer]) == ("fixture", peer)
        with pytest.raises(BoardError):
            restored.authenticate(old[peer])
    assert restored.unread("fixture", "two") == {"global": 0, "direct": 0}
    before = len(restored.export("fixture", 0)["events"])
    assert restored.execute("fixture", "one", request) == result
    assert len(restored.export("fixture", 0)["events"]) == before
    original.engine.dispose()
    restored.engine.dispose()


@pytest.mark.parametrize("damage", ["hash", "run", "roster", "bytes"])
def test_restore_rejects_corrupt_or_wrong_snapshot(tmp_path, damage):
    """Fail closed before serving corrupt, oversized or wrong-identity board state."""
    path, database, tokens, _, _ = populated_board(tmp_path)
    bundle = snapshot_board(path, 1_000_000)
    run = "fixture"
    if damage == "hash":
        bundle["sha256"] = "0" * 64
    elif damage == "run":
        run = "other"
    elif damage == "roster":
        tokens = {"other": secrets.token_urlsafe(32)}
    elif damage == "bytes":
        bundle["raw_bytes"] = 1_000_001
    target = tmp_path / "rejected.sqlite"
    with pytest.raises(ValueError):
        restore_board(bundle, target, run, tokens, 1_000_000)
    assert not target.exists()
    database.engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_health_tracks_unfinished_database_work(tmp_path, monkeypatch):
    """Keep checkpoint admission nonzero until a delayed committed write finishes."""
    import asyncio
    import threading

    import httpx

    from collaboration_index.board.server import create_app

    _, database, tokens, request, _ = populated_board(tmp_path)
    request = {**request, "request_id": "delayed-send"}
    entered, release = threading.Event(), threading.Event()
    original = database.execute

    def delayed(*args):
        """Hold a harmless idempotent database operation until the test releases it."""
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(database, "execute", delayed)
    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://fixture"
    ) as client:
        pending = asyncio.create_task(
            client.post(
                "/rpc",
                json=request,
                headers={"Authorization": "Bearer " + tokens["one"]},
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            response = await client.get("/health/checkpoint")
            assert response.json() == {"active_requests": 1}
        finally:
            release.set()
        assert (await pending).status_code == 200
        assert (await client.get("/health/checkpoint")).json() == {"active_requests": 0}
    database.engine.dispose()
