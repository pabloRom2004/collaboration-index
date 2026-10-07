"""Verify automatic unread updates, non-destructive polling and task-specific privacy."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log

import collaboration_index.board.context as board_context
from collaboration_index.board.client import LIMITS, BoardClient, BoardConnectionError
from collaboration_index.board.database import BoardDatabase, token_hash
from collaboration_index.board.server import create_app
from collaboration_index.colouring import colouring
from collaboration_index.counting import counting
from collaboration_index.hle import hle_collaboration
from collaboration_index.smoke import fixture_model
from collaboration_index.spelling import spelling

LOGS = Path(__file__).parents[1] / "logs"
OPTIONS = {
    "url": "http://testserver",
    "run_id": "test",
    "agent_id": "agent_0",
    "token_env": "UNREAD_REMINDER_TEST_TOKEN",
}


def bind_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport
) -> None:
    """Route the real reminder client through an authored local test transport."""
    monkeypatch.setenv(OPTIONS["token_env"], "authored-private-credential")
    client = BoardClient(OPTIONS, transport)

    def configured(options: dict[str, Any]) -> BoardClient:
        """Keep the tested scoped options while substituting only the HTTP transport."""
        assert options == OPTIONS
        return client

    monkeypatch.setattr(board_context, "BoardClient", configured)


async def test_unread_poll_does_not_deliver_or_read_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep global and DM counts until explicit reads, excluding another peer's DMs."""
    database = BoardDatabase("sqlite:///" + str(tmp_path / "board.db"), LIMITS)
    database.provision(
        {
            "test": {
                "agent_0": token_hash("authored-private-credential"),
                "agent_1": token_hash("peer-one-token"),
                "agent_2": token_hash("peer-two-token"),
            }
        }
    )

    def act(actor: str, action: str, **arguments: Any) -> None:
        """Apply one authored operation through the actual board transaction boundary."""
        database.execute(
            "test", actor, {"request_id": uuid4().hex, "action": action, **arguments}
        )

    act("agent_1", "send", message="Private fixture body never shown in reminders")
    act("agent_1", "send", recipient="agent_0", message="Private direct fixture")
    act("agent_1", "send", recipient="agent_2", message="Unrelated private fixture")
    bind_client(
        monkeypatch,
        httpx.ASGITransport(app=create_app(database, token_hash("observer"))),
    )
    expected = {"global": 1, "direct": 1}
    assert database.unread("test", "agent_0") == expected
    for _ in range(2):
        message = await board_context.unread_reminder(OPTIONS)
        assert "1 unread messages from the Global Board" in message
        assert "1 unread Direct Messages" in message
        assert "Private" not in message and "agent_" not in message
        assert "authored-private-credential" not in message
        assert database.unread("test", "agent_0") == expected
    direct = await board_context.unread_reminder(OPTIONS, direct_only=True)
    assert "1 unread Direct Messages from your neighbours" in direct
    assert "read_messages" in direct and "Global" not in direct
    assert database.unread("test", "agent_0") == expected
    act("agent_0", "read")
    message = await board_context.unread_reminder(OPTIONS)
    assert "0 unread messages from the Global Board" in message
    assert "1 unread Direct Messages" in message
    act("agent_0", "read", recipient="agent_1")
    message = await board_context.unread_reminder(OPTIONS)
    assert "0 unread messages from the Global Board" in message
    assert "0 unread Direct Messages" in message


@pytest.mark.parametrize("direct_only", [False, True])
@pytest.mark.parametrize("failure", ["transport", "invalid_counts"])
async def test_failed_poll_is_sanitized_and_never_invents_zero(
    monkeypatch: pytest.MonkeyPatch, direct_only: bool, failure: str
) -> None:
    """Report unavailable counts without leaking transport details or fabricating emptiness."""

    def respond(request: httpx.Request) -> httpx.Response:
        """Fail only the count endpoint using an authored transport or malformed result."""
        assert request.method == "GET" and request.url.path == "/unread"
        if failure == "transport":
            raise httpx.ReadTimeout(
                "private diagnostic and credential", request=request
            )
        return httpx.Response(
            200,
            json={
                "run_id": "test",
                "agent_id": "agent_0",
                "result": {"global": True, "direct": -1},
            },
        )

    bind_client(monkeypatch, httpx.MockTransport(respond))
    message = await board_context.unread_reminder(OPTIONS, direct_only=direct_only)
    assert message == (
        "Unread message counts are unavailable. Use "
        + ("read_messages" if direct_only else "message_board")
        + " to check for messages."
    )
    assert "private" not in message and "0 unread" not in message


async def test_poll_deadline_and_cancellation_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound a slow count request and propagate external cancellation rather than masking it."""

    async def slow(request: httpx.Request) -> httpx.Response:
        """Hold an authored count request past its configured deadline."""
        await asyncio.sleep(1)
        raise AssertionError("The count request should have timed out")

    monkeypatch.setitem(LIMITS, "unread_timeout", 0.01)
    bind_client(monkeypatch, httpx.MockTransport(slow))
    started = time.monotonic()
    assert "unavailable" in await board_context.unread_reminder(OPTIONS)
    assert time.monotonic() - started < 0.5

    async def cancelled(request: httpx.Request) -> httpx.Response:
        """Simulate task cancellation during the reminder's metadata request."""
        raise asyncio.CancelledError

    bind_client(monkeypatch, httpx.MockTransport(cancelled))
    with pytest.raises(asyncio.CancelledError):
        await board_context.unread_reminder(OPTIONS)


def authored_task(benchmark: str, tmp_path: Path, condition: str) -> Task:
    """Create a small real team task without loading any benchmark question data."""
    common = {
        "agents": 2,
        "token_limit_per_agent": 10000,
        "team_time_limit": 30,
        "sandbox_enabled": False,
        "artifact_dir": str(tmp_path),
        "condition": condition,
    }
    if benchmark == "hle":
        records = tmp_path / "authored.json"
        records.write_text(
            json.dumps(
                [
                    {
                        "id": "fixture-capital",
                        "question": "Capital of France?",
                        "answer": "Paris",
                    },
                    {"id": "fixture-sum", "question": "2+2?", "answer": "4"},
                ]
            )
        )
        return hle_collaboration(
            records_file=str(records), answer_judge="exact", **common
        )
    if benchmark == "counting":
        return counting(target=4, **common)
    if benchmark == "spelling":
        return spelling(candidate_count=4, **common)
    return colouring(**common)


def decision_messages(log_path: str) -> list[str]:
    """Inspect only authored fixture decision updates from a completed native evaluation."""
    sample = read_eval_log(log_path, resolve_attachments=True).samples[0]
    messages = []
    for event in sample.events:
        if event.event == "model":
            last = event.input[-1]
            assert last.role == "user" and last.text.startswith("Time update:")
            messages.append(last.text)
    assert messages
    return messages


@pytest.mark.parametrize("benchmark", ["hle", "counting", "spelling", "colouring"])
def test_every_collaborative_decision_has_live_unread_counts(
    benchmark: str, tmp_path: Path
) -> None:
    """Deliver initial, positive and cleared counts through real native peers and board HTTP."""
    task = authored_task(benchmark, tmp_path, "collaborative")
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples[0].scores["team_score"].value["quality"] == 1
    messages = decision_messages(log.location)
    assert len(messages) > 2
    for message in messages:
        assert "unread" in message and "unavailable" not in message
        assert not re.search(r"agent_\d+", message)
        assert "run_id" not in message and "total_agents" not in message
        if benchmark == "colouring":
            assert "read_messages" in message and "Global Board" not in message
        else:
            assert "Global Board" in message and "Use message_board" in message
    assert any(re.search(r"[1-9]\d* unread", message) for message in messages)
    assert any("0 unread Direct Messages" in message for message in messages)


@pytest.mark.parametrize("benchmark", ["hle", "counting", "spelling", "colouring"])
def test_oracle_decisions_have_no_poll_or_notification(
    benchmark: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve the communication-free control in every supported oracle task."""

    async def forbidden(self: BoardClient) -> dict[str, int]:
        """Reject any count poll attempted by an oracle peer."""
        raise AssertionError("An oracle peer polled its hidden board")

    monkeypatch.setattr(BoardClient, "unread", forbidden)
    task = authored_task(benchmark, tmp_path, "oracle_allocation")
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples[0].scores["team_score"].value["quality"] == 1
    assert all("unread" not in message for message in decision_messages(log.location))


def test_count_endpoint_failure_keeps_team_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continue a successful native task with a generic reminder when metadata polling fails."""

    async def unavailable(self: BoardClient) -> dict[str, int]:
        """Simulate count-only infrastructure failure while normal board tools still work."""
        raise BoardConnectionError("private endpoint diagnostic")

    monkeypatch.setattr(BoardClient, "unread", unavailable)
    task = authored_task("counting", tmp_path, "collaborative")
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples[0].scores["team_score"].value["quality"] == 1
    for message in decision_messages(log.location):
        assert "Unread message counts are unavailable" in message
        assert "private endpoint" not in message and "0 unread" not in message


@pytest.mark.docker
def test_mirrorcode_uses_the_same_decision_notifications(tmp_path: Path) -> None:
    """Verify unread updates while the existing authored MirrorCode fixture shares and scores code."""
    pytest.importorskip("mc")
    from test_mirrorcode import team_fixture

    from collaboration_index.mirrorcode import mirrorcode

    task = mirrorcode(
        agents=2,
        token_limit_per_agent=10000,
        team_time_limit=600,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=team_fixture(), log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples[0].scores["mirrorcode_scorer"].value["all"] == 1.0
    assert all("Global Board" in message for message in decision_messages(log.location))
