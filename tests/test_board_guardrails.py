"""Exercise bounded board resources with harmless messages and mock inference."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import ToolError
from sqlalchemy import func, select

from collaboration_index.board.client import LIMITS, BoardClient, message_board
from collaboration_index.board.database import (
    BoardBusy,
    BoardDatabase,
    BoardFull,
    events,
    receipts,
    requests,
    runs,
    token_hash,
)
from collaboration_index.board.server import create_app


def make_board(tmp_path, **limits):
    """Provision a tiny durable board with two trusted slots and adjustable resource limits."""
    url = "sqlite:///" + str(tmp_path / "bounded.db")
    database = BoardDatabase(url, LIMITS | limits)
    database.provision(
        {"test": {actor: token_hash(actor + "-token") for actor in ("a", "b")}}
    )
    return database, url


def call(database, actor, action, request_id=None, **args):
    """Apply one harmless operation through the production transaction boundary."""
    return database.execute(
        "test",
        actor,
        {"request_id": request_id or uuid4().hex, "action": action, **args},
    )


def usage(database):
    """Read only retained-byte metadata for the test collaboration run."""
    with database.engine.connect() as db:
        return db.execute(
            select(runs.c.retained_bytes).where(runs.c.id == "test")
        ).scalar_one()


def test_fullness_is_transactional_and_history_stays_readable(tmp_path):
    """Reject concurrent excess writes while retaining readable/exportable history and retries."""
    database, url = make_board(tmp_path, storage_bytes=60000, read_cache_bytes=8192)
    before = usage(database)
    original = call(database, "a", "send", request_id="original", message="hello")
    cost = usage(database) - before
    database.limits["storage_bytes"] = (
        usage(database) + cost * 2 + database.limits["read_cache_bytes"]
    )

    def write(index):
        """Return only whether a concurrently attempted harmless post was committed."""
        try:
            call(database, "b", "send", message="hello", request_id=f"post-{index}")
            return True
        except BoardFull as error:
            assert str(error) == "Message board full"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(write, range(16)))
    assert sum(accepted) == 2
    assert (
        usage(database) + database.limits["read_cache_bytes"]
        <= database.limits["storage_bytes"]
    )
    assert (
        call(database, "a", "send", request_id="original", message="hello") == original
    )
    before_read = usage(database)
    assert database.unread("test", "a")["global"] == 2
    page = call(database, "a", "read", request_id="lost-read")
    assert len(page["messages"]) == 3
    assert database.unread("test", "a")["global"] == 0
    assert usage(database) == before_read
    exported = database.export("test", 0)
    assert sum(row["kind"] == "message" for row in exported["events"]) == 3
    assert any(row["kind"] == "read" for row in exported["events"])
    restored = BoardDatabase(url, database.limits)
    assert call(restored, "a", "read", request_id="lost-read") == page
    assert restored.unread("test", "a")["global"] == 0
    with pytest.raises(BoardFull):
        call(restored, "b", "send", message="one more")


def test_read_cache_and_receipts_do_not_duplicate_retained_text(tmp_path, monkeypatch):
    """Bound repeated reads, expire only retry snapshots, and preserve durable write idempotency."""
    database, url = make_board(tmp_path, storage_bytes=60000, read_cache_bytes=2048)
    sent = call(
        database, "b", "send", request_id="durable-post", message="authored greeting"
    )
    first = call(database, "a", "read", request_id="first-read")
    assert call(database, "a", "read", request_id="first-read") == first
    before = database.export("test", 0)
    accepted = 0
    while True:
        try:
            call(database, "a", "read")
            accepted += 1
        except BoardBusy:
            break
        assert accepted < 10
    assert database.export("test", 0) == before
    with database.engine.connect() as db:
        cache = (
            db.execute(select(requests).where(requests.c.expires_at.is_not(None)))
            .mappings()
            .all()
        )
        assert sum(row["storage_bytes"] for row in cache) <= 2048
        assert all("authored greeting" not in row["result"] for row in cache)
        assert db.execute(select(func.count()).select_from(receipts)).scalar_one() == 1
    import collaboration_index.board.database as module

    now = module.time.time()
    monkeypatch.setattr(
        module.time, "time", lambda: now + LIMITS["read_cache_seconds"] + 1
    )
    assert len(call(database, "a", "read")["messages"]) == 1
    restored = BoardDatabase(url, database.limits)
    assert (
        call(
            restored,
            "b",
            "send",
            request_id="durable-post",
            message="authored greeting",
        )
        == sent
    )
    assert restored.export("test", 0) == before


@pytest.mark.parametrize("text", ["\0" * 64000, "🧪" * 64000])
def test_byte_pages_allow_maximum_message_and_never_skip(tmp_path, text):
    """Retrieve maximum valid messages with byte and count bounds and exact cursor progression."""
    database, _ = make_board(tmp_path)
    for _ in range(4):
        call(database, "b", "send", message=text)
    for key, fetch in (
        ("messages", lambda after: call(database, "a", "read", after=after)),
        ("events", lambda after: database.export("test", after)),
    ):
        cursor = 0
        sequences = []
        while True:
            page = fetch(cursor)
            assert (
                len(
                    json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()
                )
                <= LIMITS["page_bytes"]
            )
            assert len(page[key]) <= 50
            assert page[key]
            sequences.extend(
                entry["sequence"] for entry in page[key] if entry["kind"] == "message"
            )
            assert page["next_after"] == page[key][-1]["sequence"]
            cursor = page["next_after"]
            if not page["has_more"]:
                break
        assert len(sequences) == 4 and sequences == sorted(set(sequences))
    assert database.unread("test", "a")["global"] == 0


async def test_request_and_wait_overload_recover_after_cancellation(
    tmp_path, monkeypatch
):
    """Release wait/request capacity after cancellation, error and successful completion."""
    database, _ = make_board(tmp_path, concurrent_requests=2, concurrent_waits=1)
    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    headers = {"Authorization": "Bearer a-token"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        waiting = asyncio.create_task(
            http.post(
                "/rpc",
                json={"request_id": "wait", "action": "wait", "wait_seconds": 25},
                headers=headers,
            )
        )
        await asyncio.sleep(0.05)
        rejected = await http.post(
            "/rpc", json={"request_id": "wait-2", "action": "wait"}, headers=headers
        )
        assert (
            rejected.status_code == 429
            and rejected.json()["detail"] == "Board busy; try again later"
        )
        assert (await http.get("/unread", headers=headers)).status_code == 200
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert (
            await http.post(
                "/rpc",
                json={"request_id": "wait-3", "action": "wait", "wait_seconds": 0},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await http.post("/rpc", content="invalid", headers=headers)
        ).status_code == 422
        assert (await http.get("/unread", headers=headers)).status_code == 200

    started = threading.Event()
    release = threading.Event()
    original = database.authenticate

    def stalled_auth(token):
        """Hold harmless authentication in a bounded test thread to exercise request admission."""
        started.set()
        assert release.wait(2)
        return original(token)

    monkeypatch.setattr(database, "authenticate", stalled_auth)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        held = [
            asyncio.create_task(http.get("/unread", headers=headers)) for _ in range(2)
        ]
        await asyncio.to_thread(started.wait, 1)
        assert (await http.get("/unread", headers=headers)).status_code == 429
        held[0].cancel()
        await asyncio.sleep(0.02)
        assert not held[0].done()
        assert (await http.get("/unread", headers=headers)).status_code == 429
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await held[0]
        assert (await held[1]).status_code == 200
        assert (await http.get("/unread", headers=headers)).status_code == 200


async def test_body_cursor_and_database_wait_bounds_are_sanitized(
    tmp_path, monkeypatch
):
    """Reject oversized JSON before decoding and return bounded sanitized database-stall errors."""
    database, _ = make_board(tmp_path, request_body_bytes=512, database_seconds=0.05)
    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    headers = {"Authorization": "Bearer a-token"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        assert (
            await http.post("/rpc", content=b"x" * 513, headers=headers)
        ).json() == {"detail": "Board request too large"}
        oversized = {"request_id": "cursor", "action": "read", "after": 2**100}
        assert (await http.post("/rpc", json=oversized, headers=headers)).json() == {
            "detail": "Invalid board arguments"
        }
        with database.engine.begin() as lock:
            lock.exec_driver_sql("UPDATE board_runs SET sequence=sequence")
            response = await http.post(
                "/rpc",
                json={
                    "request_id": "blocked",
                    "action": "send",
                    "message": "secret fixture text",
                },
                headers=headers,
            )
            assert response.status_code == 503
            assert response.json() == {"detail": "Board busy; try again later"}
        assert (
            await http.post(
                "/rpc",
                json={"request_id": "recovered", "action": "send", "message": "hello"},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await http.get(
                "/admin/export/test",
                params={"after": 2**100},
                headers={"Authorization": "Bearer observer"},
            )
        ).status_code == 400


async def test_framework_query_validation_never_echoes_untrusted_values(tmp_path):
    """Sanitize malformed observer query values before returning a framework error."""
    database, _ = make_board(tmp_path)
    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        response = await http.get(
            "/admin/export/test",
            params={"after": "untrusted fixture secret"},
            headers={"Authorization": "Bearer observer"},
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid board arguments"}


def test_inspect_recovers_from_full_and_busy_tool_errors(tmp_path, monkeypatch):
    """Continue a harmless Inspect trajectory after recoverable full and busy tool responses."""
    database, _ = make_board(tmp_path, storage_bytes=60000, read_cache_bytes=8192)
    call(database, "b", "send", message="hello")
    database.limits["storage_bytes"] = (
        usage(database) + database.limits["read_cache_bytes"]
    )
    app = create_app(database, token_hash("observer"))
    inner = httpx.ASGITransport(app=app)
    seen_busy = False

    async def respond(request):
        """Inject one overload response while retaining the real board for other mock calls."""
        nonlocal seen_busy
        if json.loads(request.content)["action"] == "agents" and not seen_busy:
            seen_busy = True
            return httpx.Response(429, json={"detail": "Board busy; try again later"})
        return await inner.handle_async_request(request)

    monkeypatch.setenv("BOARD_GUARDRAIL_TEST", "a-token")
    options = {
        "url": "http://testserver",
        "run_id": "test",
        "agent_id": "a",
        "token_env": "BOARD_GUARDRAIL_TEST",
    }
    operations = iter(
        [
            {"action": "send", "message": "too full"},
            {"action": "agents"},
            {"action": "read"},
        ]
    )
    errors = []

    def output(messages, tools, tool_choice, config):
        """Script harmless calls and verify that Inspect returns failures to the model."""
        errors.extend(
            message.error.message
            for message in messages
            if message.role == "tool" and message.error is not None
        )
        args = next(operations, None)
        if args is None:
            return ModelOutput.from_content("mockllm/model", "Complete")
        return ModelOutput.for_tool_call("mockllm/model", "message_board", args)

    task = Task(
        dataset=[Sample(input="Exercise a harmless shared message board")],
        solver=[
            use_tools(message_board(options, httpx.MockTransport(respond))),
            generate(),
        ],
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=output, memoize=False),
        display="none",
        score=False,
        log_dir=str(tmp_path / "logs"),
    )
    assert log.status == "success" and log.samples[0].error is None
    assert {"Message board full", "Board busy; try again later"} <= set(errors)
    assert database.unread("test", "a")["global"] == 0
    assert all(
        call["completed"] is False
        for call in log.samples[0].store["BoardHistory:calls"][:2]
    )
    assert log.samples[0].store["BoardHistory:calls"][-1]["completed"] is True


@pytest.mark.parametrize("body", ["json", "text", "list"])
@pytest.mark.parametrize(
    "status,detail",
    [
        (409, "Message board full"),
        (429, "Board busy; try again later"),
        (503, "Board busy; try again later"),
    ],
)
async def test_resource_errors_are_recoverable_client_tool_errors(
    monkeypatch, status, detail, body
):
    """Return resource rejections as ToolError without transport retries or fatal sample failures."""
    monkeypatch.setenv("BOARD_GUARDRAIL_TEST", "token")
    calls = []

    def respond(request):
        """Return one harmless resource rejection and count bounded client calls."""
        calls.append(request)
        if body == "text":
            return httpx.Response(status, text="untrusted proxy response")
        return httpx.Response(status, json=[] if body == "list" else {"detail": detail})

    client = BoardClient(
        {
            "url": "http://testserver",
            "run_id": "test",
            "agent_id": "a",
            "token_env": "BOARD_GUARDRAIL_TEST",
        },
        httpx.MockTransport(respond),
    )
    with pytest.raises(ToolError, match=detail):
        await client.call({"request_id": "test", "action": "send"})
    assert len(calls) == 1


def test_existing_database_upgrade_compacts_read_retries_without_losing_history(
    tmp_path,
):
    """Migrate legacy accounting/cache columns while retaining unread state and write retries."""
    database, url = make_board(tmp_path)
    sent = call(database, "b", "send", request_id="durable", message="hello")
    first = call(database, "a", "read", request_id="legacy-read")
    with database.engine.begin() as db:
        db.exec_driver_sql(
            "UPDATE board_requests SET result=? WHERE id='legacy-read'",
            (json.dumps(first),),
        )
        db.exec_driver_sql("DROP INDEX board_request_expiry")
        db.exec_driver_sql("ALTER TABLE board_requests DROP COLUMN expires_at")
        db.exec_driver_sql("ALTER TABLE board_requests DROP COLUMN storage_bytes")
        db.exec_driver_sql("ALTER TABLE board_runs DROP COLUMN retained_bytes")
    database.engine.dispose()
    restored = BoardDatabase(url, LIMITS)
    assert call(restored, "a", "read", request_id="legacy-read") == first
    assert call(restored, "b", "send", request_id="durable", message="hello") == sent
    assert restored.unread("test", "a")["global"] == 0
    assert usage(restored) > 0
    with restored.engine.connect() as db:
        cached = (
            db.execute(select(requests).where(requests.c.id == "legacy-read"))
            .mappings()
            .one()
        )
        assert (
            "message_sequences" in cached["result"] and "hello" not in cached["result"]
        )
        assert cached["expires_at"] is not None and cached["storage_bytes"] > 0
        assert (
            db.execute(
                select(func.count())
                .select_from(events)
                .where(events.c.kind == "message")
            ).scalar_one()
            == 1
        )
    with pytest.raises(ValueError, match="fresh run"):
        restored.provision({"test": {"c": token_hash("c-token")}})


async def test_streaming_body_limit_and_upload_timeout_release_admission(tmp_path):
    """Reject oversized chunked or stalled uploads without decoding JSON or leaking slots."""
    database, _ = make_board(
        tmp_path,
        request_body_bytes=512,
        request_body_seconds=0.02,
        concurrent_requests=2,
        concurrent_waits=1,
    )
    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    headers = {"Authorization": "Bearer a-token"}

    async def oversized():
        """Yield harmless chunks whose combined size exceeds the body bound."""
        yield b"x" * 300
        yield b"x" * 300

    async def stalled():
        """Hold a harmless upload beyond its configured local deadline."""
        yield b"x"
        await asyncio.sleep(0.1)
        yield b"x"

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        assert (
            await http.post("/rpc", content=oversized(), headers=headers)
        ).status_code == 413
        assert (
            await http.post("/rpc", content=stalled(), headers=headers)
        ).status_code == 408
        assert (await http.get("/unread", headers=headers)).status_code == 200


def test_operator_limits_file_overrides_defaults(tmp_path, monkeypatch):
    """Apply trusted operator overrides through the real application factory."""
    import yaml

    from collaboration_index.board.server import application

    credentials = tmp_path / "credentials.json"
    credentials.write_text(
        json.dumps(
            {
                "runs": {"test": {"a": token_hash("a-token")}},
                "admin_hash": token_hash("observer"),
            }
        )
    )
    settings = tmp_path / "limits.yaml"
    settings.write_text(
        yaml.safe_dump({"storage_bytes": 60000, "read_cache_bytes": 2048})
    )
    monkeypatch.setenv("BOARD_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv(
        "BOARD_DATABASE_URL", "sqlite:///" + str(tmp_path / "configured.db")
    )
    monkeypatch.setenv("BOARD_LIMITS_FILE", str(settings))
    app = application()
    assert app.user_middleware[0].kwargs["limits"]["storage_bytes"] == 60000
    settings.write_text("unknown_setting: 1\n")
    with pytest.raises(ValueError, match="known configuration"):
        application()
    for key in (
        "request_timeout",
        "request_attempts",
        "retry_seconds",
        "unread_timeout",
    ):
        settings.write_text(yaml.safe_dump({key: 1}))
        with pytest.raises(ValueError, match="Client timing settings"):
            application()


def test_cache_rejection_rolls_back_new_receipts_and_recovers_after_expiry(
    tmp_path, monkeypatch
):
    """Keep unread messages unread when a response cannot be cached, then recover cleanly."""
    import collaboration_index.board.database as module

    database, _ = make_board(tmp_path, storage_bytes=60000, read_cache_bytes=2048)
    call(database, "b", "send", message="first")
    while True:
        try:
            call(database, "a", "read")
        except BoardBusy:
            break
    later = call(database, "b", "send", message="later")
    assert database.unread("test", "a")["global"] == 1
    before = database.export("test", 0)
    with pytest.raises(BoardBusy):
        call(database, "a", "read", after=later["sequence"] - 1)
    assert database.unread("test", "a")["global"] == 1
    assert database.export("test", 0) == before
    now = module.time.time()
    monkeypatch.setattr(
        module.time, "time", lambda: now + LIMITS["read_cache_seconds"] + 1
    )
    page = call(database, "a", "read", after=later["sequence"] - 1)
    assert [entry["sequence"] for entry in page["messages"]] == [later["sequence"]]
    assert database.unread("test", "a")["global"] == 0


async def test_client_total_attempt_deadline_matches_retry_cache_window(monkeypatch):
    """Bound each complete transport attempt so trickled responses cannot outlive retry retention."""
    from collaboration_index.board.client import BoardConnectionError

    monkeypatch.setenv("BOARD_GUARDRAIL_TEST", "token")
    monkeypatch.setitem(LIMITS, "request_timeout", 0.01)
    monkeypatch.setitem(LIMITS, "request_attempts", 2)
    monkeypatch.setitem(LIMITS, "retry_seconds", 0)
    seen = []

    async def respond(request):
        """Simulate a harmless remote response that exceeds the whole-attempt deadline."""
        seen.append(json.loads(request.content))
        await asyncio.sleep(0.2)
        return httpx.Response(
            200, json={"run_id": "test", "agent_id": "a", "result": {}}
        )

    client = BoardClient(
        {
            "url": "http://testserver",
            "run_id": "test",
            "agent_id": "a",
            "token_env": "BOARD_GUARDRAIL_TEST",
        },
        httpx.MockTransport(respond),
    )
    with pytest.raises(BoardConnectionError, match="bounded retries"):
        await asyncio.wait_for(
            client.call({"request_id": "stable", "action": "read"}), timeout=0.1
        )
    assert len(seen) == 2 and seen[0] == seen[1]


def test_inspect_recovers_after_board_transport_exhaustion(tmp_path, monkeypatch):
    """Preserve a committed send and continue Inspect after all reply deliveries fail."""
    database, _ = make_board(tmp_path)
    inner = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    lost_ids = []
    monkeypatch.setenv("BOARD_GUARDRAIL_TEST", "a-token")
    monkeypatch.setitem(LIMITS, "retry_seconds", 0)

    async def respond(request):
        """Commit a harmless send through the real board but lose every transport reply."""
        body = json.loads(request.content)
        response = await inner.handle_async_request(request)
        if body["action"] == "send":
            lost_ids.append(body["request_id"])
            raise httpx.ReadError("Synthetic lost reply")
        return response

    operations = iter(
        [
            {"action": "send", "message": "harmless lost reply fixture"},
            {"action": "read"},
        ]
    )
    errors = []

    def output(messages, tools, tool_choice, config):
        """Read the board after the ambiguous send instead of blindly sending again."""
        errors.extend(m.error.message for m in messages if m.role == "tool" and m.error)
        args = next(operations, None)
        return (
            ModelOutput.for_tool_call("mockllm/model", "message_board", args)
            if args
            else ModelOutput.from_content("mockllm/model", "Complete")
        )

    options = {
        "url": "http://testserver",
        "run_id": "test",
        "agent_id": "a",
        "token_env": "BOARD_GUARDRAIL_TEST",
    }
    task = Task(
        dataset=[Sample(input="Exercise harmless board transport recovery")],
        solver=[
            use_tools(message_board(options, httpx.MockTransport(respond))),
            generate(),
        ],
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=output, memoize=False),
        display="none",
        score=False,
        log_dir=str(tmp_path / "logs"),
    )
    assert log.status == "success" and log.samples[0].error is None
    assert len(lost_ids) == LIMITS["request_attempts"] and len(set(lost_ids)) == 1
    assert any("may have completed" in error for error in errors)
    page = call(database, "a", "read")
    assert len(page["messages"]) == 1
    calls = log.samples[0].store["BoardHistory:calls"]
    assert calls[0]["completed"] is False and calls[-1]["completed"] is True


@pytest.mark.parametrize("failure", ["unauthorized", "identity", "json", "result"])
async def test_board_identity_and_protocol_failures_remain_fatal(monkeypatch, failure):
    """Keep authentication and untrusted response failures outside recoverable transport errors."""
    from collaboration_index.board.client import BoardConnectionError

    monkeypatch.setenv("BOARD_GUARDRAIL_TEST", "a-token")

    async def respond(request):
        """Return one harmless malformed authentication or protocol fixture."""
        if failure == "unauthorized":
            return httpx.Response(401)
        if failure == "json":
            return httpx.Response(200, content=b"invalid")
        return httpx.Response(
            200,
            json={
                "run_id": "other" if failure == "identity" else "test",
                "agent_id": "a",
                "result": [] if failure == "result" else {},
            },
        )

    client = BoardClient(
        {
            "url": "http://testserver",
            "run_id": "test",
            "agent_id": "a",
            "token_env": "BOARD_GUARDRAIL_TEST",
        },
        httpx.MockTransport(respond),
    )
    with pytest.raises(BoardConnectionError) as error:
        await client.call({"request_id": "fixture", "action": "read"})
    assert not isinstance(error.value, ToolError)
